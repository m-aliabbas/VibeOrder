import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

/// Shared-device sign in: pick yourself, enter PIN. Throttled server-side.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  List<Map<String, dynamic>>? staff;
  Map<String, dynamic>? selected;
  String pin = '';
  String? error;
  bool busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final app = context.read<AppState>();
    setState(() => error = app.lastError);
    app.lastError = null;
    try {
      final list = await app.staffDirectory();
      if (mounted) setState(() => staff = list);
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> _submit() async {
    if (selected == null || pin.isEmpty) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await context.read<AppState>().login(selected!['username'], pin);
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          error = e.message;
          pin = '';
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _key(String k) {
    setState(() {
      if (k == '⌫') {
        if (pin.isNotEmpty) pin = pin.substring(0, pin.length - 1);
      } else if (k == '✓') {
        _submit();
      } else if (pin.length < 8) {
        pin += k;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final app = context.read<AppState>();
    return Scaffold(
      appBar: AppBar(
        title: Text(app.prefs.getString('branch_name') ?? 'VibeOrder'),
        actions: [
          PopupMenuButton<String>(
            onSelected: (v) async {
              if (v == 'unpair') await app.unpair();
            },
            itemBuilder: (_) => [const PopupMenuItem(value: 'unpair', child: Text('Unpair this device'))],
          ),
        ],
      ),
      body: staff == null
          ? Center(
              child: error != null
                  ? Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(error!, style: TextStyle(color: t.colorScheme.error)),
                        const SizedBox(height: 12),
                        FilledButton(onPressed: _load, child: const Text('Retry')),
                      ],
                    )
                  : const CircularProgressIndicator(),
            )
          : LayoutBuilder(
              builder: (context, c) {
                final wide = c.maxWidth > 760;
                final people = _people(t);
                final pad = _pinPad(t);
                return wide
                    ? Row(
                        children: [
                          Expanded(child: people),
                          const VerticalDivider(width: 1),
                          SizedBox(width: 380, child: pad),
                        ],
                      )
                    : selected == null
                    ? people
                    : pad;
              },
            ),
    );
  }

  Widget _people(ThemeData t) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Who is signing in?', style: t.textTheme.titleLarge),
        const SizedBox(height: 12),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final s in staff!)
              SizedBox(
                width: 200,
                height: 88,
                child: ChoiceChip(
                  key: ValueKey('staff-${s['username']}'),
                  showCheckmark: false,
                  selected: selected?['username'] == s['username'],
                  onSelected: (_) => setState(() {
                    selected = s;
                    pin = '';
                    error = null;
                  }),
                  label: SizedBox(
                    width: 170,
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(s['display_name'], style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                        Text((s['roles'] as List).join(', '), style: t.textTheme.bodySmall),
                      ],
                    ),
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }

  Widget _pinPad(ThemeData t) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (selected == null)
              Text('Choose your name', style: t.textTheme.titleMedium)
            else ...[
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(selected!['display_name'], style: t.textTheme.titleMedium),
                  IconButton(onPressed: () => setState(() => selected = null), icon: const Icon(Icons.close)),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                pin.isEmpty ? 'Enter PIN' : '•' * pin.length,
                key: const ValueKey('pin-display'),
                style: t.textTheme.headlineMedium?.copyWith(letterSpacing: 8),
              ),
              SizedBox(
                height: 28,
                child: error == null ? null : Text(error!, style: TextStyle(color: t.colorScheme.error)),
              ),
              const SizedBox(height: 8),
              NumPad(onKey: _key, extra: '✓'),
              const SizedBox(height: 16),
              SizedBox(
                width: 240,
                child: FilledButton(
                  key: const ValueKey('sign-in'),
                  onPressed: busy || pin.isEmpty ? null : _submit,
                  child: busy ? const CircularProgressIndicator() : const Text('Sign in'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
