import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/print_log.dart';
import 'stock_screen.dart';

/// Alert centre: computed by the branch server from live state (stock, kitchen, printers, refunds, backups).
/// An alert disappears when its cause is fixed; nothing here is dismissed by hand.
class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key, this.onOpenStock});

  /// Opens the Stock screen on a tab (0 levels, 1 recipes, 2 orders) — supplied by the home screen.
  final void Function(int tab)? onOpenStock;

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  List<Map<String, dynamic>>? alerts;
  Timer? timer;
  String? error;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    timer = Timer.periodic(const Duration(seconds: 10), (_) => _load());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('alerts');
      if (!mounted) return;
      setState(() {
        alerts = List<Map<String, dynamic>>.from(r.data['alerts']);
        error = null;
      });
      app.setAlertCounts(Map<String, dynamic>.from(r.data['counts']));
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> _act(Map<String, dynamic> a) async {
    final action = a['action'] as Map<String, dynamic>?;
    if (action == null) return;
    switch (action['type']) {
      case 'sellout':
        final targets = List<Map<String, dynamic>>.from(action['targets']);
        final chosen = await showDialog<List<Map<String, dynamic>>>(
          context: context,
          builder: (_) => _SelloutDialog(alert: a, targets: targets),
        );
        if (chosen == null || chosen.isEmpty || !mounted) return;
        var done = 0;
        for (final t in chosen) {
          if (!mounted) return;
          if (await setAvailability(context, t['kind'], t['code'], false, a['title'])) done++;
        }
        if (mounted) showInfo(context, '$done item${done == 1 ? '' : 's'} marked sold out');
      case 'reprint':
        await showPrintJob(context, action['job']);
      case 'receive':
        widget.onOpenStock?.call(2);
        return;
      case 'recipes':
        widget.onOpenStock?.call(1);
        return;
    }
    await _load();
  }

  static const _actionLabels = {
    'sellout': 'Mark sold out',
    'reprint': 'Open ticket',
    'receive': 'Order more',
    'recipes': 'Add recipes',
  };

  static const _categoryIcons = {
    'stock': Icons.inventory_outlined,
    'kitchen': Icons.soup_kitchen_outlined,
    'print': Icons.print_disabled_outlined,
    'cash': Icons.payments_outlined,
    'system': Icons.dns_outlined,
  };

  Color _color(ThemeData t, String s) => switch (s) {
    'critical' => t.colorScheme.error,
    'warning' => Colors.orange.shade700,
    _ => t.colorScheme.primary,
  };

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final list = alerts;
    if (list == null) {
      return Center(child: error == null ? const CircularProgressIndicator() : Text(error!));
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text('Not refreshed: $error', style: TextStyle(color: t.colorScheme.error)),
            ),
          if (list.isEmpty)
            Padding(
              padding: const EdgeInsets.all(48),
              child: Column(
                children: [
                  Icon(Icons.check_circle_outline, size: 56, color: Colors.green.shade600),
                  const SizedBox(height: 12),
                  Text('All clear', style: t.textTheme.titleLarge),
                  const Text('Stock, kitchen tickets and printers look fine.'),
                ],
              ),
            ),
          for (final a in list)
            Card(
              key: ValueKey('alert-${a['id']}'),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
                side: BorderSide(color: _color(t, a['severity']).withValues(alpha: 0.6)),
              ),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(4, 4, 12, 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    ListTile(
                      leading: Icon(
                        _categoryIcons[a['category']] ?? Icons.notifications,
                        color: _color(t, a['severity']),
                      ),
                      title: Text(a['title'], style: const TextStyle(fontWeight: FontWeight.w700)),
                      subtitle: Text(
                        [
                          a['detail'],
                          if (a['at'] != null) 'since ${DateFormat('HH:mm').format(DateTime.parse(a['at']).toLocal())}',
                        ].join(' · '),
                      ),
                      trailing: StatusChip(a['severity'].toString().toUpperCase(), color: _color(t, a['severity'])),
                    ),
                    if (a['action'] != null && _actionLabels.containsKey(a['action']['type']))
                      Align(
                        alignment: Alignment.centerRight,
                        child: FilledButton.tonal(
                          key: ValueKey('alert-action-${a['id']}'),
                          onPressed: () => _act(a),
                          child: Text(_actionLabels[a['action']['type']]!),
                        ),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _SelloutDialog extends StatefulWidget {
  const _SelloutDialog({required this.alert, required this.targets});
  final Map<String, dynamic> alert;
  final List<Map<String, dynamic>> targets;

  @override
  State<_SelloutDialog> createState() => _SelloutDialogState();
}

class _SelloutDialogState extends State<_SelloutDialog> {
  late final picked = {for (final t in widget.targets) '${t['kind']}:${t['code']}'};

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Stop selling?'),
      content: SizedBox(
        width: 400,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.alert['title']),
            const SizedBox(height: 8),
            const Text('Waiters and the online store will see these as sold out until you switch them back on.'),
            for (final t in widget.targets)
              CheckboxListTile(
                key: ValueKey('sellout-${t['code']}'),
                value: picked.contains('${t['kind']}:${t['code']}'),
                title: Text(t['name']),
                subtitle: Text(t['kind'] == 'option' ? 'Topping / option' : 'Menu item'),
                onChanged: (v) => setState(() {
                  final k = '${t['kind']}:${t['code']}';
                  v == true ? picked.add(k) : picked.remove(k);
                }),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('sellout-confirm'),
          onPressed: picked.isEmpty
              ? null
              : () => Navigator.pop(context, [
                  for (final t in widget.targets)
                    if (picked.contains('${t['kind']}:${t['code']}')) t,
                ]),
          child: Text('Mark ${picked.length} sold out'),
        ),
      ],
    );
  }
}
