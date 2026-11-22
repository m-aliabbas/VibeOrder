import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api/api_client.dart';

final _fmt = NumberFormat.decimalPattern('en_PK');

/// PKR minor units -> "Rs 1,900" (shows paisa only when present).
String money(num? minor, {String currency = 'PKR'}) {
  final m = (minor ?? 0).toInt();
  final neg = m < 0;
  final abs = m.abs();
  final rupees = abs ~/ 100;
  final paisa = abs % 100;
  final s = 'Rs ${_fmt.format(rupees)}${paisa == 0 ? '' : '.${paisa.toString().padLeft(2, '0')}'}';
  return neg ? '-$s' : s;
}

/// Parse "1,900" / "1900.50" rupees into minor units.
int? parseRupees(String text) {
  final t = text.replaceAll(',', '').replaceAll('Rs', '').trim();
  if (t.isEmpty) return null;
  final v = double.tryParse(t);
  if (v == null || v < 0) return null;
  return (v * 100).round();
}

void showError(BuildContext context, Object e) {
  final msg = e is ApiError ? e.message : e.toString();
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor: Theme.of(context).colorScheme.error,
        behavior: SnackBarBehavior.floating,
      ),
    );
}

void showInfo(BuildContext context, String msg) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(msg), behavior: SnackBarBehavior.floating));
}

class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.trailing});
  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Row(
        children: [
          Expanded(
            child: Text(
              text.toUpperCase(),
              style: t.textTheme.labelMedium?.copyWith(letterSpacing: 1.1, color: t.colorScheme.onSurfaceVariant),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip(this.label, {super.key, this.color, this.icon});
  final String label;
  final Color? color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final c = color ?? Theme.of(context).colorScheme.secondary;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: c.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: c.withValues(alpha: 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[Icon(icon, size: 14, color: c), const SizedBox(width: 4)],
          Text(
            label,
            style: TextStyle(color: c, fontWeight: FontWeight.w600, fontSize: 12),
          ),
        ],
      ),
    );
  }
}

/// Numeric PIN/amount pad with 64px keys for busy hands.
class NumPad extends StatelessWidget {
  const NumPad({super.key, required this.onKey, this.extra = '', this.size = 72});
  final void Function(String key) onKey;
  final String extra;
  final double size;

  @override
  Widget build(BuildContext context) {
    final keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', extra, '0', '⌫'];
    return SizedBox(
      width: size * 3 + 24,
      child: Wrap(
        spacing: 12,
        runSpacing: 12,
        children: [
          for (final k in keys)
            SizedBox(
              width: size,
              height: size * 0.8,
              child: k.isEmpty
                  ? const SizedBox()
                  : FilledButton.tonal(
                      key: ValueKey('key-$k'),
                      style: FilledButton.styleFrom(
                        padding: EdgeInsets.zero,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                      onPressed: () => onKey(k),
                      child: Text(k, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w600)),
                    ),
            ),
        ],
      ),
    );
  }
}

/// Category look when a dish has no photo yet: a warm gradient with an icon.
IconData categoryIcon(String? code) => switch (code) {
  'pizza' => Icons.local_pizza,
  'deals' => Icons.local_offer,
  'karahi' => Icons.soup_kitchen,
  'bbq' => Icons.outdoor_grill,
  'rice' => Icons.rice_bowl,
  'bread' => Icons.bakery_dining,
  'drinks' => Icons.local_drink,
  'sides' => Icons.fastfood,
  'extras' => Icons.eco,
  _ => Icons.restaurant,
};

class DishImage extends StatelessWidget {
  const DishImage({super.key, required this.url, this.category, this.radius = 0});
  final String? url;
  final String? category;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final fallback = Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [t.colorScheme.primary.withValues(alpha: 0.22), t.colorScheme.secondary.withValues(alpha: 0.18)],
        ),
      ),
      alignment: Alignment.center,
      child: Icon(categoryIcon(category), size: 40, color: t.colorScheme.primary.withValues(alpha: 0.7)),
    );
    final img = url == null
        ? fallback
        : Image.network(
            url!,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => fallback,
            frameBuilder: (_, child, frame, sync) => AnimatedOpacity(
              opacity: sync || frame != null ? 1 : 0,
              duration: const Duration(milliseconds: 250),
              child: child,
            ),
          );
    return ClipRRect(borderRadius: BorderRadius.circular(radius), child: img);
  }
}
