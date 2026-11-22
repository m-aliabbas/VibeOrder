import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/visuals.dart';

/// Everyone signs in with their own username and password. No pairing: the first sign in on a device
/// registers it with the branch server. The server address is remembered and only needed once.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final TextEditingController server;
  late final TextEditingController user;
  final pass = TextEditingController();
  final passFocus = FocusNode();
  bool showServer = false;
  bool hide = true;
  bool busy = false;
  String? error;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    server = TextEditingController(text: app.server);
    user = TextEditingController(text: app.lastUsername);
    error = app.lastError;
    app.lastError = null;
  }

  @override
  void dispose() {
    server.dispose();
    user.dispose();
    pass.dispose();
    passFocus.dispose();
    super.dispose();
  }

  Future<void> _submit({String? branch}) async {
    if (user.text.trim().isEmpty || pass.text.isEmpty) {
      setState(() => error = 'Enter your username and password');
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await context.read<AppState>().login(server.text, user.text, pass.text, branch: branch);
    } on ChooseBranch catch (c) {
      if (!mounted) return;
      final picked = await showDialog<String>(
        context: context,
        builder: (ctx) => SimpleDialog(
          title: const Text('Which branch are you working at?'),
          children: [
            for (final b in c.branches)
              SimpleDialogOption(
                key: ValueKey('branch-${b['code']}'),
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                onPressed: () => Navigator.pop(ctx, b['code'] as String),
                child: Text(b['name'] ?? b['code'], style: const TextStyle(fontSize: 17)),
              ),
          ],
        ),
      );
      if (picked != null && mounted) {
        setState(() => busy = false);
        return _submit(branch: picked);
      }
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          error = e.status == 0 ? 'Cannot reach the server at ${server.text}. Check the address and Wi-Fi.' : e.message;
          if (e.status == 0) showServer = true;
          if (e.code == 'invalid_credentials') pass.clear();
        });
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final app = context.watch<AppState>();
    final wide = MediaQuery.sizeOf(context).width >= 860;

    final form = ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 400),
      child: AutofillGroup(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (!wide) ...[
              const Align(alignment: Alignment.centerLeft, child: BrandMark(size: 52, onDark: false)),
              const SizedBox(height: 16),
            ],
            Text('Sign in', style: t.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            Text(
              app.branchName.isEmpty ? 'Use your own username and password.' : app.branchName,
              style: t.textTheme.bodyLarge?.copyWith(color: t.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: 28),
            TextField(
              key: const ValueKey('username'),
              controller: user,
              autofocus: user.text.isEmpty,
              autocorrect: false,
              textInputAction: TextInputAction.next,
              autofillHints: const [AutofillHints.username],
              decoration: const InputDecoration(labelText: 'Username', prefixIcon: Icon(Icons.person_outline)),
              onSubmitted: (_) => passFocus.requestFocus(),
            ),
            const SizedBox(height: 14),
            TextField(
              key: const ValueKey('password'),
              controller: pass,
              focusNode: passFocus,
              autofocus: user.text.isNotEmpty,
              obscureText: hide,
              autofillHints: const [AutofillHints.password],
              textInputAction: TextInputAction.go,
              decoration: InputDecoration(
                labelText: 'Password',
                prefixIcon: const Icon(Icons.lock_outline),
                suffixIcon: IconButton(
                  tooltip: hide ? 'Show password' : 'Hide password',
                  icon: Icon(hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                  onPressed: () => setState(() => hide = !hide),
                ),
              ),
              onSubmitted: (_) => _submit(),
            ),
            AnimatedSize(
              duration: const Duration(milliseconds: 150),
              child: error == null
                  ? const SizedBox(height: 20)
                  : Padding(
                      padding: const EdgeInsets.only(top: 12, bottom: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(Icons.error_outline, color: t.colorScheme.error, size: 20),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              error!,
                              key: const ValueKey('login-error'),
                              style: TextStyle(color: t.colorScheme.error, fontWeight: FontWeight.w600),
                            ),
                          ),
                        ],
                      ),
                    ),
            ),
            const SizedBox(height: 8),
            FilledButton(
              key: const ValueKey('sign-in'),
              onPressed: busy ? null : _submit,
              child: busy
                  ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : const Text('Sign in'),
            ),
            const SizedBox(height: 20),
            if (showServer)
              TextField(
                key: const ValueKey('server'),
                controller: server,
                keyboardType: TextInputType.url,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'Server address',
                  helperText: 'The restaurant PC running VibeOrder, e.g. http://192.168.1.10:8000',
                  prefixIcon: Icon(Icons.dns_outlined),
                ),
              )
            else
              Center(
                child: TextButton.icon(
                  key: const ValueKey('change-server'),
                  onPressed: () => setState(() => showServer = true),
                  icon: const Icon(Icons.dns_outlined, size: 18),
                  label: Text('Server: ${server.text.replaceFirst(RegExp(r'^https?://'), '')}'),
                ),
              ),
            const SizedBox(height: 8),
            Text(
              'Forgot your password? Ask your manager to reset it.',
              textAlign: TextAlign.center,
              style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );

    if (!wide) {
      // Phone / small tablet: the food photo fills the screen and the form floats on frosted glass.
      return Scaffold(
        body: PhotoBackdrop(
          image: AppImages.loginHero,
          opacities: const [0.2, 0.45, 0.85],
          child: SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(20),
                child: GlassPanel(child: form),
              ),
            ),
          ),
        ),
      );
    }
    return Scaffold(
      body: Row(
        children: [
          Expanded(flex: 6, child: _BrandPanel(branch: app.branchName)),
          Expanded(
            flex: 5,
            child: Center(
              child: SingleChildScrollView(padding: const EdgeInsets.all(40), child: form),
            ),
          ),
        ],
      ),
    );
  }
}

class _BrandPanel extends StatelessWidget {
  const _BrandPanel({required this.branch});
  final String branch;

  @override
  Widget build(BuildContext context) {
    return PhotoBackdrop(
      image: AppImages.loginHero,
      opacities: const [0.1, 0.35, 0.92],
      child: Padding(
        padding: const EdgeInsets.all(48),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                BrandMark(size: 44),
                SizedBox(width: 12),
                Text(
                  'VibeOrder',
                  style: TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w900),
                ),
              ],
            ),
            const Spacer(),
            Text(
              branch.isEmpty ? 'Orders, kitchen and cash\nin one place.' : branch,
              style: const TextStyle(color: Colors.white, fontSize: 38, fontWeight: FontWeight.w800, height: 1.12),
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                for (final f in const [
                  (Icons.wifi_off_rounded, 'Works offline'),
                  (Icons.soup_kitchen_outlined, 'Live kitchen'),
                  (Icons.print_outlined, 'Thermal printing'),
                  (Icons.insights_outlined, 'Daily insights'),
                ])
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(999),
                      border: Border.all(color: Colors.white.withValues(alpha: 0.25)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(f.$1, color: Colors.white, size: 16),
                        const SizedBox(width: 6),
                        Text(
                          f.$2,
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
