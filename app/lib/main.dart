import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'state/app_state.dart';
import 'widgets/visuals.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  runApp(VibeOrderApp(state: AppState(prefs)));
}

ThemeData buildTheme(Brightness b) {
  const seed = Color(0xFFC2410C); // terracotta: tandoor clay
  final dark = b == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: seed,
    brightness: b,
    primary: dark ? const Color(0xFFFF8A4C) : seed,
    secondary: dark ? const Color(0xFFFFC34D) : const Color(0xFFB7791F),
    surface: dark ? const Color(0xFF17120F) : const Color(0xFFFBF7F2),
    surfaceContainerLowest: dark ? const Color(0xFF120E0B) : Colors.white,
    surfaceContainerLow: dark ? const Color(0xFF1E1814) : const Color(0xFFF6F0E8),
    surfaceContainer: dark ? const Color(0xFF241D18) : const Color(0xFFF1E9DF),
    surfaceContainerHighest: dark ? const Color(0xFF30271F) : const Color(0xFFE9DED1),
    outlineVariant: dark ? const Color(0xFF3A3029) : const Color(0xFFE6DACC),
  );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme, brightness: b);
  final text = base.textTheme
      .apply(bodyColor: scheme.onSurface, displayColor: scheme.onSurface)
      .copyWith(
        headlineSmall: base.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.3),
        titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.2),
        titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
      );
  final radius = BorderRadius.circular(16);
  return base.copyWith(
    textTheme: text,
    scaffoldBackgroundColor: scheme.surface,
    visualDensity: VisualDensity.standard,
    materialTapTargetSize: MaterialTapTargetSize.padded,
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: text.titleLarge,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        side: BorderSide(color: scheme.outlineVariant),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(textStyle: const TextStyle(fontWeight: FontWeight.w700)),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      color: scheme.surfaceContainerLowest,
      surfaceTintColor: Colors.transparent,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(color: scheme.outlineVariant),
      ),
    ),
    chipTheme: ChipThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
      side: BorderSide(color: scheme.outlineVariant),
      labelStyle: const TextStyle(fontWeight: FontWeight.w600),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: scheme.surfaceContainerLow,
      indicatorColor: scheme.primary.withValues(alpha: 0.16),
      selectedIconTheme: IconThemeData(color: scheme.primary),
      selectedLabelTextStyle: TextStyle(color: scheme.primary, fontWeight: FontWeight.w800, fontSize: 12),
      unselectedLabelTextStyle: TextStyle(color: scheme.onSurfaceVariant, fontWeight: FontWeight.w600, fontSize: 12),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: scheme.surfaceContainerLow,
      indicatorColor: scheme.primary.withValues(alpha: 0.16),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: scheme.surfaceContainerLowest,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    ),
    dividerTheme: DividerThemeData(color: scheme.outlineVariant, space: 1),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surfaceContainerLowest,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
    ),
    listTileTheme: const ListTileThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
    ),
  );
}

class VibeOrderApp extends StatelessWidget {
  const VibeOrderApp({super.key, required this.state, this.splash = const Duration(milliseconds: 1400)});
  final AppState state;

  /// How long the splash shows at least on start (zero in widget tests).
  final Duration splash;

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider.value(
      value: state,
      child: MaterialApp(
        title: 'VibeOrder',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        themeMode: ThemeMode.light,
        home: _Root(splash: splash),
      ),
    );
  }
}

class _Root extends StatefulWidget {
  const _Root({required this.splash});
  final Duration splash;

  @override
  State<_Root> createState() => _RootState();
}

class _RootState extends State<_Root> {
  late bool splashing = widget.splash > Duration.zero;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    if (app.hasSavedSession) app.restoreSession();
    if (splashing) Future.delayed(widget.splash, () => mounted ? setState(() => splashing = false) : null);
  }

  @override
  Widget build(BuildContext context) {
    final stage = context.select<AppState, Stage>((s) => s.stage);
    final restoring = context.select<AppState, bool>((s) => s.restoring);
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 450),
      switchInCurve: Curves.easeOut,
      child: splashing || restoring
          ? const SplashView(key: ValueKey('splash-view'))
          : switch (stage) {
              Stage.login => const LoginScreen(),
              Stage.ready => const HomeScreen(),
            },
    );
  }
}
