import 'dart:ui';

import 'package:flutter/material.dart';

/// Bundled photography (generated, see assets/images/README.md). Every use has a gradient fallback, so a
/// missing file never breaks a screen.
class AppImages {
  static const loginHero = 'assets/images/login_hero.jpg';
  static const splash = 'assets/images/splash_bg.jpg';
  static const dashboard = 'assets/images/dashboard_banner.jpg';
  static const kitchen = 'assets/images/kitchen_bg.jpg';
}

/// Full-bleed photo with a colour overlay: dark at the text side, the brand ember glow at the other.
class PhotoBackdrop extends StatelessWidget {
  const PhotoBackdrop({
    super.key,
    required this.image,
    this.child,
    this.begin = Alignment.topCenter,
    this.end = Alignment.bottomCenter,
    this.stops = const [0.0, 0.55, 1.0],
    this.opacities = const [0.15, 0.55, 0.92],
    this.blur = 0,
    this.tint,
    this.alignment = Alignment.center,
  });

  final String image;
  final Widget? child;
  final Alignment begin, end, alignment;
  final List<double> stops;
  final List<double> opacities;
  final double blur;
  final Color? tint;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final base = tint ?? const Color(0xFF120B07);
    Widget photo = Image.asset(
      image,
      fit: BoxFit.cover,
      alignment: alignment,
      gaplessPlayback: true,
      errorBuilder: (_, _, _) => DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [t.colorScheme.primary, Color.lerp(t.colorScheme.primary, Colors.black, 0.7)!],
          ),
        ),
      ),
    );
    if (blur > 0) {
      photo = ImageFiltered(
        imageFilter: ImageFilter.blur(sigmaX: blur, sigmaY: blur),
        child: photo,
      );
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        photo,
        // Ember glow from one corner keeps the brand colour in every photo.
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              center: const Alignment(-0.9, -0.9),
              radius: 1.3,
              colors: [t.colorScheme.primary.withValues(alpha: 0.35), Colors.transparent],
            ),
          ),
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: begin,
              end: end,
              stops: stops,
              colors: [for (final o in opacities) base.withValues(alpha: o)],
            ),
          ),
        ),
        ?child,
      ],
    );
  }
}

/// Frosted glass panel for content that sits on a photo.
class GlassPanel extends StatelessWidget {
  const GlassPanel({super.key, required this.child, this.padding = const EdgeInsets.all(28), this.radius = 24});
  final Widget child;
  final EdgeInsets padding;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
        child: Container(
          padding: padding,
          decoration: BoxDecoration(
            color: t.colorScheme.surface.withValues(alpha: 0.86),
            borderRadius: BorderRadius.circular(radius),
            border: Border.all(color: Colors.white.withValues(alpha: 0.35)),
            boxShadow: [
              BoxShadow(color: Colors.black.withValues(alpha: 0.25), blurRadius: 40, offset: const Offset(0, 18)),
            ],
          ),
          child: child,
        ),
      ),
    );
  }
}

/// Logo mark: pizza slice in a glowing ember circle.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 56, this.onDark = true});
  final double size;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [const Color(0xFFFF8A4C), t.colorScheme.primary],
        ),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFFFF7A1A).withValues(alpha: onDark ? 0.55 : 0.3),
            blurRadius: size * 0.6,
          ),
        ],
      ),
      child: Icon(Icons.local_pizza_rounded, color: Colors.white, size: size * 0.56),
    );
  }
}

/// Splash: oven photo, brand mark scaling in, name fading up. Shown while the app restores the session.
class SplashView extends StatefulWidget {
  const SplashView({super.key, this.message});
  final String? message;

  @override
  State<SplashView> createState() => _SplashViewState();
}

class _SplashViewState extends State<SplashView> with SingleTickerProviderStateMixin {
  late final AnimationController c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))
    ..forward();

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final mark = CurvedAnimation(
      parent: c,
      curve: const Interval(0, 0.6, curve: Curves.easeOutBack),
    );
    final text = CurvedAnimation(
      parent: c,
      curve: const Interval(0.35, 1, curve: Curves.easeOut),
    );
    return Scaffold(
      backgroundColor: const Color(0xFF120B07),
      body: PhotoBackdrop(
        key: const ValueKey('splash'),
        image: AppImages.splash,
        opacities: const [0.35, 0.55, 0.9],
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ScaleTransition(scale: mark, child: const BrandMark(size: 96)),
              const SizedBox(height: 22),
              FadeTransition(
                opacity: text,
                child: SlideTransition(
                  position: Tween(begin: const Offset(0, 0.4), end: Offset.zero).animate(text),
                  child: Column(
                    children: [
                      const Text(
                        'VibeOrder',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 40,
                          fontWeight: FontWeight.w900,
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        widget.message ?? 'Orders · Kitchen · Cash',
                        style: TextStyle(color: Colors.white.withValues(alpha: 0.75), fontSize: 16, letterSpacing: 2),
                      ),
                      const SizedBox(height: 28),
                      SizedBox(
                        width: 120,
                        child: LinearProgressIndicator(
                          minHeight: 3,
                          borderRadius: BorderRadius.circular(2),
                          color: const Color(0xFFFF8A4C),
                          backgroundColor: Colors.white.withValues(alpha: 0.15),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Photo banner with a greeting, used at the top of the dashboard.
class HeroBanner extends StatelessWidget {
  const HeroBanner({super.key, required this.title, required this.subtitle, this.trailing, this.height = 150});
  final String title, subtitle;
  final Widget? trailing;
  final double height;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        height: height,
        child: PhotoBackdrop(
          image: AppImages.dashboard,
          alignment: Alignment.centerRight,
          begin: Alignment.centerLeft,
          end: Alignment.centerRight,
          stops: const [0, 0.55, 1],
          opacities: const [0.92, 0.6, 0.05],
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 18),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: const TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w800),
                      ),
                      const SizedBox(height: 4),
                      Text(subtitle, style: TextStyle(color: Colors.white.withValues(alpha: 0.8), fontSize: 15)),
                    ],
                  ),
                ),
                ?trailing,
              ],
            ),
          ),
        ),
      ),
    );
  }
}

String greeting([DateTime? now]) {
  final h = (now ?? DateTime.now()).hour;
  if (h < 5) return 'Late night';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}
