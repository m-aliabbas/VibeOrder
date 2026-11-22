import 'dart:async';
import 'dart:math' as math;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/visuals.dart';

/// Manager dashboard: today's numbers at a glance, then trends. One `GET dashboard?days=` call.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

/// Categorical colours, fixed order so a channel keeps its colour between visits.
const chartColors = [
  Color(0xFFC2410C), // terracotta
  Color(0xFF0F766E), // teal
  Color(0xFFD97706), // amber
  Color(0xFF4F46E5), // indigo
  Color(0xFF65A30D), // green
  Color(0xFF64748B), // slate
];

/// 14 -> "14 min", 905 -> "15 h".
String minutes(num m) => m >= 90 ? '${(m / 60).round()} h' : '${m.round()} min';

String compactMoney(num minor) {
  final r = minor / 100;
  if (r.abs() >= 1e6) return 'Rs ${(r / 1e6).toStringAsFixed(r.abs() >= 1e7 ? 0 : 1)}M';
  if (r.abs() >= 1e3) return 'Rs ${(r / 1e3).toStringAsFixed(r.abs() >= 1e4 ? 0 : 1)}k';
  return 'Rs ${r.round()}';
}

class _DashboardScreenState extends State<DashboardScreen> {
  static const ranges = [(1, 'Today'), (7, '7 days'), (30, '30 days'), (90, '90 days')];
  int days = 7;
  Map<String, dynamic>? data;
  String? error;
  bool loading = false;
  Timer? timer;

  @override
  void initState() {
    super.initState();
    _load();
    timer = Timer.periodic(const Duration(seconds: 60), (_) => _load(quiet: true));
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) setState(() => loading = true);
    try {
      final r = await context.read<AppState>().api.get('dashboard', query: {'days': '$days'});
      if (mounted) {
        setState(() {
          data = r.data;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted && !quiet) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final d = data;
    final width = MediaQuery.sizeOf(context).width;
    final cols = width >= 1200 ? 3 : (width >= 760 ? 2 : 1);
    return Scaffold(
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            HeroBanner(
              key: const ValueKey('dash-banner'),
              title: '${greeting()}, ${context.read<AppState>().staffName.split(' ').first}',
              subtitle: d == null ? 'Dashboard' : 'Dashboard · ${d['branch']['name']}',
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 16,
              runSpacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              alignment: WrapAlignment.spaceBetween,
              children: [
                Text(
                  d == null ? ' ' : _rangeLabel(d['range']),
                  style: t.textTheme.titleMedium?.copyWith(color: t.colorScheme.onSurfaceVariant),
                ),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SegmentedButton<int>(
                      key: const ValueKey('dash-range'),
                      showSelectedIcon: false,
                      segments: [for (final r in ranges) ButtonSegment(value: r.$1, label: Text(r.$2))],
                      selected: {days},
                      onSelectionChanged: (s) {
                        setState(() => days = s.first);
                        _load();
                      },
                    ),
                    const SizedBox(width: 8),
                    IconButton(
                      tooltip: 'Refresh',
                      onPressed: loading ? null : _load,
                      icon: loading
                          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.refresh),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 16),
            if (error != null && d == null)
              Card(
                child: ListTile(
                  leading: Icon(Icons.cloud_off, color: t.colorScheme.error),
                  title: Text(error!),
                  trailing: TextButton(onPressed: _load, child: const Text('Try again')),
                ),
              ),
            if (d == null && error == null)
              const Padding(
                padding: EdgeInsets.all(80),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (d != null) ...[
              _KpiRow(k: Map<String, dynamic>.from(d['kpis']), days: days),
              const SizedBox(height: 16),
              _Grid(
                cols: cols,
                children: [
                  _Panel(
                    title: days == 1 ? 'Sales by hour' : 'Sales per day',
                    span: cols >= 2 ? 2 : 1,
                    child: days == 1
                        ? _HourBars(rows: List<Map<String, dynamic>>.from(d['by_hour']), valueKey: 'net_minor')
                        : _SalesLine(rows: List<Map<String, dynamic>>.from(d['sales_by_day'])),
                  ),
                  _Panel(
                    title: 'Where orders come from',
                    child: _Donut(
                      rows: [
                        for (final c in d['channels'])
                          (c['name'] as String, (c['net_minor'] as num).toDouble(), '${c['orders']} orders'),
                      ],
                    ),
                  ),
                  if (days != 1)
                    _Panel(
                      title: 'Busy hours',
                      subtitle: 'Orders taken per hour, all days in range',
                      span: cols >= 2 ? 2 : 1,
                      child: _HourBars(rows: List<Map<String, dynamic>>.from(d['by_hour']), valueKey: 'orders'),
                    ),
                  _Panel(
                    title: 'Payments',
                    child: _Donut(
                      rows: [
                        for (final p in d['payments'])
                          (p['name'] as String, (p['amount_minor'] as num).toDouble(), '${p['count']} payments'),
                      ],
                    ),
                  ),
                  _Panel(
                    title: 'Best sellers',
                    child: _RankBars(
                      rows: [
                        for (final i in d['top_items'])
                          (i['name'] as String, (i['quantity'] as num).toDouble(), money(i['net_minor'])),
                      ],
                      format: (v) => '${v.round()} sold',
                    ),
                  ),
                  _Panel(
                    title: 'Staff',
                    subtitle: 'Sales by who took the order',
                    child: _RankBars(
                      rows: [
                        for (final s in d['staff'])
                          (s['name'] as String, (s['net_minor'] as num).toDouble(), '${s['orders']} orders'),
                      ],
                      format: (v) => compactMoney(v),
                      color: chartColors[1],
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  String _rangeLabel(Map r) {
    final f = DateFormat('d MMM');
    final from = DateTime.parse(r['from']), to = DateTime.parse(r['to']);
    return r['days'] == 1 ? 'Today, ${f.format(to)}' : '${f.format(from)} – ${f.format(to)}';
  }
}

// ---------------------------------------------------------------- KPI tiles

class _KpiRow extends StatelessWidget {
  const _KpiRow({required this.k, required this.days});
  final Map<String, dynamic> k;
  final int days;

  @override
  Widget build(BuildContext context) {
    final vs = days == 1 ? 'vs yesterday' : 'vs previous $days days';
    final late = (k['late_tickets'] as num).toInt();
    final tiles = [
      _Kpi(
        key: const ValueKey('kpi-sales'),
        label: 'Net sales',
        value: money(k['net_minor']),
        change: k['net_change_pct'],
        note: vs,
        icon: Icons.payments_outlined,
      ),
      _Kpi(
        label: 'Orders',
        value: '${k['orders']}',
        change: k['orders_change_pct'],
        note: vs,
        icon: Icons.receipt_long,
      ),
      _Kpi(
        label: 'Average order',
        value: money(k['avg_minor']),
        change: k['avg_change_pct'],
        note: vs,
        icon: Icons.shopping_basket_outlined,
      ),
      _Kpi(
        label: 'Open now',
        value: '${k['open_checks']}',
        note: '${money(k['open_value_minor'])} not paid yet',
        icon: Icons.table_restaurant_outlined,
      ),
      _Kpi(
        label: 'In the kitchen',
        value: '${k['in_kitchen']} items',
        note: late > 0 ? '$late tickets late' : 'nothing late',
        alert: late > 0,
        icon: Icons.soup_kitchen_outlined,
      ),
      _Kpi(
        label: 'Kitchen time',
        value: k['kitchen_median_min'] == null ? '–' : minutes(k['kitchen_median_min'] as num),
        note: k['kitchen_p90_min'] == null ? 'sent → ready' : '9 in 10 within ${minutes(k['kitchen_p90_min'] as num)}',
        icon: Icons.timer_outlined,
      ),
    ];
    return LayoutBuilder(
      builder: (context, c) {
        final per = c.maxWidth >= 1100 ? 6 : (c.maxWidth >= 640 ? 3 : 2);
        const gap = 12.0;
        final w = (c.maxWidth - gap * (per - 1)) / per;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [for (final x in tiles) SizedBox(width: w, child: x)],
        );
      },
    );
  }
}

class _Kpi extends StatelessWidget {
  const _Kpi({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
    this.change,
    this.note,
    this.alert = false,
  });
  final String label, value;
  final IconData icon;
  final num? change;
  final String? note;
  final bool alert;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final up = (change ?? 0) >= 0;
    final changeColor = up ? const Color(0xFF15803D) : t.colorScheme.error;
    return Card(
      color: alert ? t.colorScheme.errorContainer : null,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 18, color: t.colorScheme.onSurfaceVariant),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    label,
                    style: t.textTheme.labelLarge?.copyWith(color: t.colorScheme.onSurfaceVariant),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Text(
                value,
                style: t.textTheme.headlineSmall?.copyWith(fontFeatures: const [FontFeature.tabularFigures()]),
              ),
            ),
            const SizedBox(height: 4),
            Row(
              children: [
                if (change != null) ...[
                  Icon(up ? Icons.arrow_upward : Icons.arrow_downward, size: 14, color: changeColor),
                  Text(
                    '${change!.abs().toStringAsFixed(change!.abs() >= 10 ? 0 : 1)}% ',
                    style: TextStyle(color: changeColor, fontWeight: FontWeight.w700, fontSize: 12),
                  ),
                ],
                Expanded(
                  child: Text(
                    note ?? '',
                    style: t.textTheme.bodySmall?.copyWith(
                      color: alert ? t.colorScheme.onErrorContainer : t.colorScheme.onSurfaceVariant,
                      fontWeight: alert ? FontWeight.w700 : null,
                    ),
                    overflow: TextOverflow.ellipsis,
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

// ---------------------------------------------------------------- layout

class _Grid extends StatelessWidget {
  const _Grid({required this.cols, required this.children});
  final int cols;
  final List<_Panel> children;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, c) {
        const gap = 16.0;
        final unit = (c.maxWidth - gap * (cols - 1)) / cols;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [
            for (final p in children)
              SizedBox(width: unit * math.min(p.span, cols) + gap * (math.min(p.span, cols) - 1), child: p),
          ],
        );
      },
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.title, required this.child, this.subtitle, this.span = 1});
  final String title;
  final String? subtitle;
  final Widget child;
  final int span;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: t.textTheme.titleMedium),
            if (subtitle != null)
              Text(subtitle!, style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
            const SizedBox(height: 14),
            SizedBox(height: 240, child: child),
          ],
        ),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty();

  @override
  Widget build(BuildContext context) => Center(
    child: Text('No sales in this period yet', style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant)),
  );
}

FlTitlesData _titles(ThemeData t, {required Widget Function(double) bottom, required String Function(double) left}) {
  final style = TextStyle(fontSize: 11, color: t.colorScheme.onSurfaceVariant);
  return FlTitlesData(
    topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
    rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
    leftTitles: AxisTitles(
      sideTitles: SideTitles(
        showTitles: true,
        reservedSize: 52,
        getTitlesWidget: (v, meta) => v == meta.max
            ? const SizedBox.shrink()
            : Padding(
                padding: const EdgeInsets.only(right: 6),
                child: Text(left(v), style: style, textAlign: TextAlign.right),
              ),
      ),
    ),
    bottomTitles: AxisTitles(
      sideTitles: SideTitles(
        showTitles: true,
        reservedSize: 26,
        interval: 1,
        getTitlesWidget: (v, meta) => Padding(
          padding: const EdgeInsets.only(top: 6),
          child: DefaultTextStyle(style: style, child: bottom(v)),
        ),
      ),
    ),
  );
}

FlGridData _grid(ThemeData t) => FlGridData(
  drawVerticalLine: false,
  getDrawingHorizontalLine: (_) => FlLine(color: t.colorScheme.outlineVariant, strokeWidth: 1, dashArray: [4, 4]),
);

// ---------------------------------------------------------------- charts

class _SalesLine extends StatelessWidget {
  const _SalesLine({required this.rows});
  final List<Map<String, dynamic>> rows;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    if (rows.every((r) => r['net_minor'] == 0)) return const _Empty();
    final dates = [for (final r in rows) DateTime.parse(r['date'])];
    final maxY = rows.map((r) => (r['net_minor'] as num).toDouble()).reduce(math.max);
    final step = math.max(1, (rows.length / 7).ceil());
    final color = t.colorScheme.primary;
    return LineChart(
      LineChartData(
        minY: 0,
        maxY: maxY * 1.15,
        gridData: _grid(t),
        borderData: FlBorderData(show: false),
        titlesData: _titles(
          t,
          left: compactMoney,
          bottom: (v) {
            final i = v.round();
            if (i < 0 || i >= dates.length || i % step != 0) return const SizedBox.shrink();
            return Text(rows.length <= 14 ? DateFormat('E d').format(dates[i]) : DateFormat('d MMM').format(dates[i]));
          },
        ),
        lineTouchData: LineTouchData(
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (_) => t.colorScheme.inverseSurface,
            getTooltipItems: (spots) => [
              for (final s in spots)
                LineTooltipItem(
                  '${DateFormat('EEE d MMM').format(dates[s.x.round()])}\n${money(s.y)} · ${rows[s.x.round()]['orders']} orders',
                  TextStyle(color: t.colorScheme.onInverseSurface, fontWeight: FontWeight.w600),
                ),
            ],
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: [
              for (var i = 0; i < rows.length; i++) FlSpot(i.toDouble(), (rows[i]['net_minor'] as num).toDouble()),
            ],
            isCurved: true,
            preventCurveOverShooting: true,
            color: color,
            barWidth: 3,
            dotData: FlDotData(show: rows.length <= 14),
            belowBarData: BarAreaData(
              show: true,
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [color.withValues(alpha: 0.28), color.withValues(alpha: 0.02)],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _HourBars extends StatelessWidget {
  const _HourBars({required this.rows, required this.valueKey});
  final List<Map<String, dynamic>> rows;
  final String valueKey;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final active = rows.where((r) => (r[valueKey] as num) > 0).map((r) => r['hour'] as int).toList();
    if (active.isEmpty) return const _Empty();
    final lo = math.max(0, active.reduce(math.min) - 1), hi = math.min(23, active.reduce(math.max) + 1);
    final shown = rows.where((r) => r['hour'] >= lo && r['hour'] <= hi).toList();
    final maxV = shown.map((r) => (r[valueKey] as num).toDouble()).reduce(math.max);
    final isMoney = valueKey.endsWith('_minor');
    String hourLabel(int h) => h == 0 ? '12a' : (h < 12 ? '${h}a' : (h == 12 ? '12p' : '${h - 12}p'));
    return BarChart(
      BarChartData(
        maxY: maxV * 1.15,
        gridData: _grid(t),
        borderData: FlBorderData(show: false),
        titlesData: _titles(
          t,
          left: (v) => isMoney ? compactMoney(v) : v.round().toString(),
          bottom: (v) => Text(hourLabel(v.round())),
        ),
        barTouchData: BarTouchData(
          touchTooltipData: BarTouchTooltipData(
            getTooltipColor: (_) => t.colorScheme.inverseSurface,
            getTooltipItem: (g, _, rod, _) => BarTooltipItem(
              '${hourLabel(g.x)}–${hourLabel((g.x + 1) % 24)}\n${isMoney ? money(rod.toY) : '${rod.toY.round()} orders'}',
              TextStyle(color: t.colorScheme.onInverseSurface, fontWeight: FontWeight.w600),
            ),
          ),
        ),
        barGroups: [
          for (final r in shown)
            BarChartGroupData(
              x: r['hour'],
              barRods: [
                BarChartRodData(
                  toY: (r[valueKey] as num).toDouble(),
                  width: 14,
                  color: (r[valueKey] as num) >= maxV * 0.8
                      ? t.colorScheme.primary
                      : t.colorScheme.primary.withValues(alpha: 0.45),
                  borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _Donut extends StatefulWidget {
  const _Donut({required this.rows});
  final List<(String, double, String)> rows;

  @override
  State<_Donut> createState() => _DonutState();
}

class _DonutState extends State<_Donut> {
  int? touched;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final rows = widget.rows.where((r) => r.$2 > 0).toList();
    if (rows.isEmpty) return const _Empty();
    final total = rows.fold(0.0, (a, r) => a + r.$2);
    final focus = touched != null && touched! < rows.length ? rows[touched!] : null;
    return Row(
      children: [
        Expanded(
          child: Stack(
            alignment: Alignment.center,
            children: [
              PieChart(
                PieChartData(
                  sectionsSpace: 2,
                  centerSpaceRadius: 54,
                  pieTouchData: PieTouchData(
                    touchCallback: (e, r) => setState(() {
                      touched = e.isInterestedForInteractions ? r?.touchedSection?.touchedSectionIndex : null;
                      if (touched != null && touched! < 0) touched = null;
                    }),
                  ),
                  sections: [
                    for (var i = 0; i < rows.length; i++)
                      PieChartSectionData(
                        value: rows[i].$2,
                        color: chartColors[i % chartColors.length],
                        radius: touched == i ? 30 : 24,
                        showTitle: false,
                      ),
                  ],
                ),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    focus == null ? compactMoney(total) : '${(focus.$2 * 100 / total).round()}%',
                    style: t.textTheme.titleLarge,
                  ),
                  Text(
                    focus?.$1 ?? 'total',
                    style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var i = 0; i < rows.length; i++)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(color: chartColors[i % chartColors.length], shape: BoxShape.circle),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              rows[i].$1,
                              style: const TextStyle(fontWeight: FontWeight.w700),
                              overflow: TextOverflow.ellipsis,
                            ),
                            Text(
                              '${money(rows[i].$2)} · ${rows[i].$3}',
                              style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Horizontal ranked bars with the label above each bar: easy to read on a tablet, no axis needed.
class _RankBars extends StatelessWidget {
  const _RankBars({required this.rows, required this.format, this.color});
  final List<(String, double, String)> rows;
  final String Function(double) format;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    if (rows.isEmpty) return const _Empty();
    final maxV = rows.map((r) => r.$2).reduce(math.max);
    final c = color ?? t.colorScheme.primary;
    return ListView(
      physics: const NeverScrollableScrollPhysics(),
      children: [
        for (final r in rows.take(6))
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        r.$1,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    Text(format(r.$2), style: const TextStyle(fontWeight: FontWeight.w700)),
                    const SizedBox(width: 8),
                    Text(r.$3, style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
                  ],
                ),
                const SizedBox(height: 4),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: maxV == 0 ? 0 : r.$2 / maxV,
                    minHeight: 8,
                    color: c,
                    backgroundColor: c.withValues(alpha: 0.12),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
