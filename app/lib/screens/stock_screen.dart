import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';

/// Manager stock view: theoretical on-hand per item (from the ledger), receive purchases, record losses, count.
class StockScreen extends StatefulWidget {
  const StockScreen({super.key});

  @override
  State<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends State<StockScreen> {
  List<Map<String, dynamic>> items = [];
  String filter = '';

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('stock');
      if (mounted) setState(() => items = List<Map<String, dynamic>>.from(r.data['items']));
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  String _qty(dynamic v, String unit) {
    final d = double.tryParse(v.toString()) ?? 0;
    if (unit == 'g' && d.abs() >= 1000) return '${(d / 1000).toStringAsFixed(2)} kg';
    if (unit == 'ml' && d.abs() >= 1000) return '${(d / 1000).toStringAsFixed(2)} L';
    return '${d == d.roundToDouble() ? d.toInt() : d.toStringAsFixed(1)} $unit';
  }

  Future<void> _action(Map<String, dynamic> item, String kind) async {
    final qty = TextEditingController();
    final cost = TextEditingController();
    final note = TextEditingController();
    final purchase = kind == 'receipts';
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(
          '${const {'receipts': 'Receive', 'losses': 'Record loss', 'counts': 'Count'}[kind]} · ${item['name']}',
        ),
        content: SizedBox(
          width: 380,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                key: const ValueKey('stock-qty'),
                controller: qty,
                autofocus: true,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: purchase
                      ? 'Quantity (${item['purchase_unit'].toString().isEmpty ? item['unit'] : item['purchase_unit']})'
                      : kind == 'counts'
                      ? 'Counted (${item['unit']})'
                      : 'Quantity lost (${item['unit']})',
                ),
              ),
              if (purchase)
                TextField(
                  controller: cost,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Total cost (Rs)'),
                ),
              TextField(
                controller: note,
                decoration: InputDecoration(labelText: purchase ? 'Supplier invoice' : 'Reason'),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Save')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final body = switch (kind) {
      'receipts' => {
        'reference': note.text.trim(),
        'lines': [
          {'item': item['code'], 'quantity': qty.text.trim(), 'cost_minor': parseRupees(cost.text) ?? 0},
        ],
      },
      'losses' => {'item': item['code'], 'quantity': qty.text.trim(), 'reason': note.text.trim()},
      _ => {
        'reason': note.text.trim().isEmpty ? 'count' : note.text.trim(),
        'lines': [
          {'item': item['code'], 'counted': qty.text.trim()},
        ],
      },
    };
    final r = await runCorrection(context, app.api, 'stock/$kind', body);
    if (r != null && mounted) showInfo(context, 'Saved');
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final shown = items
        .where((i) => filter.isEmpty || i['name'].toString().toLowerCase().contains(filter.toLowerCase()))
        .toList();
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: TextField(
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Find item', isDense: true),
            onChanged: (v) => setState(() => filter = v),
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              children: [
                for (final i in shown)
                  ListTile(
                    key: ValueKey('stock-${i['code']}'),
                    title: Text(i['name']),
                    subtitle: Text(
                      i['cost_missing'] == true
                          ? 'cost unknown'
                          : 'avg cost ${money(double.parse(i['avg_cost_minor'].toString()))} per ${i['unit']}',
                    ),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          _qty(i['on_hand'], i['unit']),
                          style: t.textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.w800,
                            color: (double.tryParse(i['on_hand'].toString()) ?? 0) < 0 ? t.colorScheme.error : null,
                          ),
                        ),
                        PopupMenuButton<String>(
                          onSelected: (k) => _action(i, k),
                          itemBuilder: (_) => const [
                            PopupMenuItem(value: 'receipts', child: Text('Receive purchase')),
                            PopupMenuItem(value: 'losses', child: Text('Record loss')),
                            PopupMenuItem(value: 'counts', child: Text('Count')),
                          ],
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
