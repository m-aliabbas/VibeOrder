import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import 'printers_screen.dart';

/// Branch/LAN status separate from cloud status; printer queue, last error and next action.
class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key});

  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  Map<String, dynamic>? health;
  String? error;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('devices/health');
      if (mounted) {
        setState(() {
          health = r.data;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> _reprint(Map<String, dynamic> job) async {
    final reason = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Reprint as COPY'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('First check the printer: is the original ticket already on the rail?'),
            const SizedBox(height: 12),
            TextField(
              controller: reason,
              autofocus: true,
              decoration: const InputDecoration(labelText: 'Reason'),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Print COPY')),
        ],
      ),
    );
    if (ok != true || reason.text.trim().isEmpty) return;
    try {
      await app.api.command('print-jobs/${job['id']}/reprints', {'reason': reason.text.trim()});
      if (mounted) showInfo(context, 'COPY queued');
      _load();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  String _ago(String? iso) {
    if (iso == null) return 'never';
    final d = DateTime.now().difference(DateTime.parse(iso));
    if (d.inMinutes < 2) return 'just now';
    if (d.inHours < 2) return '${d.inMinutes} min ago';
    if (d.inDays < 2) return '${d.inHours} h ago';
    return '${d.inDays} days ago';
  }

  /// A lost or stolen tablet: block it so nobody can use it, even with a valid password.
  Future<void> _block(Map<String, dynamic> d) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Block ${d['name']}?'),
        content: const Text('Everyone signed in on it is signed out and it cannot sign in again.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Block device')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await app.api.post('devices/${d['id']}/revoke', {});
      _load();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final h = health;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              _statusCard(
                t,
                'Branch server',
                error == null ? 'Connected' : 'Unreachable',
                error == null ? Colors.green : t.colorScheme.error,
                error ?? 'Server time ${h?['branch']?['server_time'] ?? ''}',
              ),
              _statusCard(
                t,
                'Cloud sync',
                h?['cloud']?['status'] == 'not_configured' ? 'Not configured' : '${h?['cloud']?['status']}',
                Colors.blueGrey,
                '${h?['cloud']?['pending_events'] ?? 0} events waiting to upload',
              ),
            ],
          ),
          SectionLabel(
            'Printers',
            trailing: app.can('printer.manage')
                ? TextButton.icon(
                    key: const ValueKey('printer-setup'),
                    onPressed: () async {
                      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PrintersScreen()));
                      _load();
                    },
                    icon: const Icon(Icons.settings_outlined, size: 18),
                    label: const Text('Printer setup'),
                  )
                : null,
          ),
          if (h != null)
            for (final p in h['printers'])
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.print,
                            color: (p['last_error'] ?? '').isEmpty ? Colors.green : t.colorScheme.error,
                          ),
                          const SizedBox(width: 8),
                          Expanded(child: Text('${p['name']} (${p['transport']})', style: t.textTheme.titleMedium)),
                          Text('${p['queued']} queued'),
                        ],
                      ),
                      if ((p['last_error'] ?? '').isNotEmpty)
                        Text('Last error: ${p['last_error']}', style: TextStyle(color: t.colorScheme.error)),
                      for (final j in p['needs_attention'])
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(
                            j['status'] == 'outcome_unknown' ? Icons.help_outline : Icons.error_outline,
                            color: Colors.orange,
                          ),
                          title: Text(
                            '${j['kind']} ${j['ticket_seq'] != null ? 'ticket ${j['ticket_seq']}' : ''} · #${j['check_token'] ?? ''} · ${j['status']}',
                          ),
                          subtitle: Text(j['next_action']),
                          trailing: app.can('print.reprint')
                              ? TextButton(onPressed: () => _reprint(j), child: const Text('Reprint COPY'))
                              : null,
                        ),
                    ],
                  ),
                ),
              ),
          const SectionLabel('Devices'),
          if (h != null)
            for (final d in h['devices'])
              ListTile(
                leading: Icon(d['kind'] == 'kds' ? Icons.tv : Icons.tablet_android),
                title: Text(d['name']),
                subtitle: Text('${d['kind']} · last seen ${_ago(d['last_seen_at'])}'),
                trailing: d['revoked']
                    ? const StatusChip('BLOCKED', color: Colors.red)
                    : app.can('device.revoke') && d['kind'] != 'system'
                    ? TextButton(onPressed: () => _block(d), child: const Text('Block'))
                    : null,
              ),
        ],
      ),
    );
  }

  Widget _statusCard(ThemeData t, String title, String value, Color color, String detail) => SizedBox(
    width: 300,
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: t.textTheme.labelLarge),
            Text(
              value,
              style: t.textTheme.titleLarge?.copyWith(color: color, fontWeight: FontWeight.w800),
            ),
            Text(detail, style: t.textTheme.bodySmall),
          ],
        ),
      ),
    ),
  );
}
