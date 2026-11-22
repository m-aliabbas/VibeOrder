import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

const roleNames = {
  'waiter': 'Waiter',
  'cashier': 'Cashier',
  'cook': 'Cook',
  'packer': 'Packer',
  'rider': 'Rider',
  'manager': 'Manager',
  'owner': 'Owner',
};

/// Manager: everyone's login. Add a person, change roles, reset a forgotten password, switch an account off.
class StaffScreen extends StatefulWidget {
  const StaffScreen({super.key});

  @override
  State<StaffScreen> createState() => _StaffScreenState();
}

class _StaffScreenState extends State<StaffScreen> {
  List<Map<String, dynamic>>? staff;
  String filter = '';
  bool showInactive = false;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('staff');
      if (mounted) setState(() => staff = List<Map<String, dynamic>>.from(r.data['staff']));
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _edit([Map<String, dynamic>? s]) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => _StaffDialog(staff: s, canMakeOwner: app.roles.contains('owner'), selfId: app.staffId),
    );
    if (saved == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final list = (staff ?? [])
        .where((s) => showInactive || s['is_active'] == true)
        .where(
          (s) => filter.isEmpty || '${s['display_name']} ${s['username']}'.toLowerCase().contains(filter.toLowerCase()),
        )
        .toList();
    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        key: const ValueKey('add-staff'),
        onPressed: () => _edit(),
        icon: const Icon(Icons.person_add_alt_1),
        label: const Text('Add person'),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 96),
          children: [
            Text('Staff', style: t.textTheme.headlineSmall),
            Text(
              'Each person signs in with their own username and password.',
              style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    decoration: const InputDecoration(
                      hintText: 'Search by name or username',
                      prefixIcon: Icon(Icons.search),
                      isDense: true,
                    ),
                    onChanged: (v) => setState(() => filter = v),
                  ),
                ),
                const SizedBox(width: 12),
                FilterChip(
                  label: const Text('Show switched off'),
                  selected: showInactive,
                  onSelected: (v) => setState(() => showInactive = v),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (staff == null)
              const Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator()),
              ),
            for (final s in list)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Card(
                  child: ListTile(
                    key: ValueKey('staff-${s['username']}'),
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    onTap: () => _edit(s),
                    leading: CircleAvatar(
                      backgroundColor: s['is_active'] == true
                          ? t.colorScheme.primary.withValues(alpha: 0.14)
                          : t.colorScheme.surfaceContainerHighest,
                      child: Text(
                        (s['display_name'] as String).isEmpty ? '?' : (s['display_name'] as String)[0].toUpperCase(),
                        style: TextStyle(color: t.colorScheme.primary, fontWeight: FontWeight.w800),
                      ),
                    ),
                    title: Text(
                      s['display_name'],
                      style: TextStyle(
                        fontWeight: FontWeight.w700,
                        decoration: s['is_active'] == true ? null : TextDecoration.lineThrough,
                      ),
                    ),
                    subtitle: Text(
                      '@${s['username']} · ${s['last_login_at'] == null ? 'never signed in' : 'last sign in ${_date(s['last_login_at'])}'}',
                    ),
                    trailing: Wrap(
                      spacing: 6,
                      children: [
                        if (s['locked'] == true) const StatusChip('LOCKED', color: Colors.red),
                        if (s['is_active'] != true) const StatusChip('OFF', color: Colors.grey),
                        for (final r in s['roles'])
                          Chip(label: Text(roleNames[r] ?? r), visualDensity: VisualDensity.compact),
                      ],
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _date(String iso) {
    final d = DateTime.parse(iso).toLocal();
    final now = DateTime.now();
    if (d.year == now.year && d.month == now.month && d.day == now.day) {
      return 'today ${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    }
    return '${d.day}/${d.month}/${d.year}';
  }
}

class _StaffDialog extends StatefulWidget {
  const _StaffDialog({required this.staff, required this.canMakeOwner, required this.selfId});
  final Map<String, dynamic>? staff;
  final bool canMakeOwner;
  final String selfId;

  @override
  State<_StaffDialog> createState() => _StaffDialogState();
}

class _StaffDialogState extends State<_StaffDialog> {
  late final TextEditingController name;
  late final TextEditingController username;
  final password = TextEditingController();
  late Set<String> roles;
  late bool active;
  bool hide = true;
  bool busy = false;
  Map<String, dynamic> fieldErrors = {};
  String? error;

  bool get isNew => widget.staff == null;
  bool get isSelf => widget.staff?['id'] == widget.selfId;

  @override
  void initState() {
    super.initState();
    final s = widget.staff;
    name = TextEditingController(text: s?['display_name'] ?? '');
    username = TextEditingController(text: s?['username'] ?? '');
    roles = Set<String>.from(s?['roles'] ?? const ['waiter']);
    active = s?['is_active'] ?? true;
  }

  Future<void> _save() async {
    setState(() {
      busy = true;
      error = null;
      fieldErrors = {};
    });
    final app = context.read<AppState>();
    try {
      if (isNew) {
        await app.api.command('staff', {
          'display_name': name.text.trim(),
          'username': username.text.trim(),
          'password': password.text,
          'roles': roles.toList(),
        });
      } else {
        final s = widget.staff!;
        await app.api.command('staff/${s['id']}', {
          if (name.text.trim() != s['display_name']) 'display_name': name.text.trim(),
          if (!_sameRoles(s['roles'])) 'roles': roles.toList(),
          if (active != s['is_active']) 'is_active': active,
          if (password.text.isNotEmpty) 'password': password.text,
        });
      }
      if (mounted) {
        Navigator.pop(context, true);
        showInfo(
          context,
          isNew
              ? '${name.text.trim()} can now sign in as ${username.text.trim()}'
              : (password.text.isNotEmpty ? 'Password changed. Tell ${name.text.trim()} the new one.' : 'Saved'),
        );
      }
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          error = e.message;
          fieldErrors = Map<String, dynamic>.from(e.body['field_errors'] ?? const {});
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  bool _sameRoles(List current) => Set<String>.from(current).containsAll(roles) && roles.length == current.length;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final available = roleNames.keys.where((r) => r != 'owner' || widget.canMakeOwner || roles.contains('owner'));
    return AlertDialog(
      title: Text(isNew ? 'Add person' : 'Edit ${widget.staff!['display_name']}'),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                key: const ValueKey('staff-name'),
                controller: name,
                autofocus: isNew,
                textCapitalization: TextCapitalization.words,
                decoration: InputDecoration(
                  labelText: 'Name',
                  errorText: fieldErrors['display_name'] == null ? null : 'Enter a name',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('staff-username'),
                controller: username,
                enabled: isNew,
                autocorrect: false,
                decoration: InputDecoration(
                  labelText: 'Username',
                  helperText: isNew ? 'Used to sign in. Letters and numbers, e.g. ali or ali.k' : null,
                  errorText: switch (fieldErrors['username']) {
                    'taken' => 'Someone already has this username',
                    null => null,
                    _ => 'Letters, numbers, dot or underscore',
                  },
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('staff-password'),
                controller: password,
                obscureText: hide,
                decoration: InputDecoration(
                  labelText: isNew ? 'Password' : 'New password (leave empty to keep)',
                  helperText: 'At least 6 characters',
                  errorText: fieldErrors['password'] == null ? null : 'At least 6 characters',
                  suffixIcon: IconButton(
                    icon: Icon(hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                    onPressed: () => setState(() => hide = !hide),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Text('What do they do?', style: t.textTheme.titleSmall),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final r in available)
                    FilterChip(
                      key: ValueKey('role-$r'),
                      label: Text(roleNames[r]!),
                      selected: roles.contains(r),
                      onSelected: (v) => setState(() => v ? roles.add(r) : roles.remove(r)),
                    ),
                ],
              ),
              if (fieldErrors['roles'] != null)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text('Pick at least one', style: TextStyle(color: t.colorScheme.error)),
                ),
              if (!isNew && !isSelf) ...[
                const SizedBox(height: 12),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Can sign in'),
                  subtitle: const Text('Switch off when someone leaves. Their history stays.'),
                  value: active,
                  onChanged: (v) => setState(() => active = v),
                ),
              ],
              if (error != null && fieldErrors.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    error!,
                    style: TextStyle(color: t.colorScheme.error, fontWeight: FontWeight.w600),
                  ),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('staff-save'),
          onPressed: busy ? null : _save,
          child: Text(isNew ? 'Add' : 'Save'),
        ),
      ],
    );
  }
}

/// Anyone: change your own password.
Future<void> showChangePassword(BuildContext context) async {
  final current = TextEditingController();
  final next = TextEditingController();
  final again = TextEditingController();
  String? error;
  await showDialog<void>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, set) => AlertDialog(
        title: const Text('Change my password'),
        content: SizedBox(
          width: 380,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: current,
                obscureText: true,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'Current password'),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: next,
                obscureText: true,
                decoration: const InputDecoration(labelText: 'New password', helperText: 'At least 6 characters'),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: again,
                obscureText: true,
                decoration: const InputDecoration(labelText: 'New password again'),
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: Text(error!, style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () async {
              if (next.text != again.text) return set(() => error = 'The new passwords do not match');
              try {
                await ctx.read<AppState>().changePassword(current.text, next.text);
                if (ctx.mounted) {
                  Navigator.pop(ctx);
                  showInfo(context, 'Password changed');
                }
              } on ApiError catch (e) {
                set(() => error = e.message);
              }
            },
            child: const Text('Change'),
          ),
        ],
      ),
    ),
  );
}
