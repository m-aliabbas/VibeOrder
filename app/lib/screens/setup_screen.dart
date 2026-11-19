import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import '../widgets/common.dart';

/// Pair this device with the branch using a short-lived single-use code from a manager.
class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key});

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  late final TextEditingController server;
  final code = TextEditingController();
  final name = TextEditingController(text: 'Tablet');
  bool busy = false;

  @override
  void initState() {
    super.initState();
    server = TextEditingController(text: context.read<AppState>().server);
  }

  Future<void> _pair() async {
    setState(() => busy = true);
    try {
      await context.read<AppState>().pair(
        server.text,
        code.text,
        name.text.trim().isEmpty ? 'Tablet' : name.text.trim(),
      );
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Icon(Icons.local_pizza_rounded, size: 64, color: t.colorScheme.primary),
                const SizedBox(height: 8),
                Text(
                  'VibeOrder',
                  textAlign: TextAlign.center,
                  style: t.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800),
                ),
                Text('Pair this device with your branch', textAlign: TextAlign.center, style: t.textTheme.bodyLarge),
                const SizedBox(height: 28),
                TextField(
                  key: const ValueKey('server'),
                  controller: server,
                  decoration: const InputDecoration(
                    labelText: 'Branch server address',
                    prefixIcon: Icon(Icons.dns_outlined),
                  ),
                  keyboardType: TextInputType.url,
                ),
                const SizedBox(height: 14),
                TextField(
                  key: const ValueKey('device-name'),
                  controller: name,
                  decoration: const InputDecoration(labelText: 'Device name', prefixIcon: Icon(Icons.tablet_android)),
                ),
                const SizedBox(height: 14),
                TextField(
                  key: const ValueKey('pair-code'),
                  controller: code,
                  decoration: const InputDecoration(
                    labelText: 'Pairing code from manager',
                    prefixIcon: Icon(Icons.key),
                  ),
                  keyboardType: TextInputType.number,
                  style: const TextStyle(fontSize: 22, letterSpacing: 4),
                  onSubmitted: (_) => _pair(),
                ),
                const SizedBox(height: 20),
                FilledButton.icon(
                  key: const ValueKey('pair-button'),
                  onPressed: busy ? null : _pair,
                  icon: busy
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.link),
                  label: const Text('Pair device'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
