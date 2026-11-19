import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';
import 'stock_screen.dart';

double _d(dynamic v) => double.tryParse(v?.toString() ?? '') ?? 0;

String _n(dynamic v) {
  final d = _d(v);
  return d == d.roundToDouble() ? d.toInt().toString() : d.toStringAsFixed(2);
}

/// "3 × 5 kg bag" when the item has a purchase unit, else "15000 g".
String packs(Map<String, dynamic> ln, dynamic baseQty) {
  final pu = (ln['purchase_unit'] ?? '').toString();
  final f = _d(ln['purchase_factor']);
  if (pu.isEmpty || f <= 0 || f == 1) return stockQty(baseQty, ln['unit']);
  return '${_n(_d(baseQty) / f)} × $pu';
}

const poStatusLabels = {
  'draft': 'Draft',
  'sent': 'Sent',
  'partial': 'Part received',
  'received': 'Received',
  'closed': 'Closed short',
  'cancelled': 'Cancelled',
};

Color poColor(ThemeData t, String s) => switch (s) {
  'draft' => t.colorScheme.secondary,
  'sent' || 'partial' => Colors.orange.shade700,
  'received' => Colors.green.shade600,
  _ => t.disabledColor,
};

// ------------------------------------------------------------------------------------------------ orders

class PurchaseOrdersTab extends StatefulWidget {
  const PurchaseOrdersTab({super.key});

  @override
  State<PurchaseOrdersTab> createState() => _PurchaseOrdersTabState();
}

class _PurchaseOrdersTabState extends State<PurchaseOrdersTab> {
  List<Map<String, dynamic>>? orders;
  List<Map<String, dynamic>> suggested = [];
  final picked = <String>{};

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final o = await app.api.get('stock/purchase-orders');
      final s = await app.api.get('stock/suggestions');
      if (!mounted) return;
      setState(() {
        orders = List<Map<String, dynamic>>.from(o.data['orders']);
        suggested = List<Map<String, dynamic>>.from(s.data['suggestions']);
        picked
          ..clear()
          ..addAll(suggested.map((x) => x['item'] as String));
      });
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _newOrder({bool fromSuggestions = false}) async {
    final lines = fromSuggestions
        ? [
            for (final s in suggested)
              if (picked.contains(s['item']))
                {
                  'item': s['item'],
                  'quantity': _n(s['suggested_packs']),
                  'expected_cost_minor': s['estimated_cost_minor'] ?? 0,
                },
          ]
        : <Map<String, dynamic>>[];
    final created = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => PurchaseOrderEditor(initialLines: lines),
    );
    await _load();
    if (created != null && mounted) await _open(created);
  }

  Future<void> _open(Map<String, dynamic> po) async {
    await showDialog(
      context: context,
      builder: (_) => PurchaseOrderDetail(orderId: po['id']),
    );
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final list = orders;
    if (list == null) return const Center(child: CircularProgressIndicator());
    final t = Theme.of(context);
    final canManage = app.can('stock.manage');
    final open = list.where((o) => ['draft', 'sent', 'partial'].contains(o['status'])).toList();
    final done = list.where((o) => !open.contains(o)).toList();
    Widget tile(Map<String, dynamic> o) => ListTile(
      key: ValueKey('po-${o['number']}'),
      title: Text('${o['number']} · ${o['supplier']?['name'] ?? 'no supplier'}'),
      subtitle: Text(
        [
          (o['lines'] as List).map((l) => l['name']).join(', '),
          if (o['expected_on'] != null) 'due ${o['expected_on']}',
        ].join(' · '),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: StatusChip(poStatusLabels[o['status']] ?? o['status'], color: poColor(t, o['status'])),
      onTap: () => _open(o),
    );
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        children: [
          if (canManage)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
              child: Align(
                alignment: Alignment.centerRight,
                child: FilledButton.tonalIcon(
                  key: const ValueKey('po-new'),
                  onPressed: () => _newOrder(),
                  icon: const Icon(Icons.add),
                  label: const Text('Purchase order'),
                ),
              ),
            ),
          if (suggested.isNotEmpty) ...[
            SectionLabel(
              'Suggested to order (${suggested.length})',
              trailing: canManage
                  ? FilledButton(
                      key: const ValueKey('po-from-suggestions'),
                      onPressed: picked.isEmpty ? null : () => _newOrder(fromSuggestions: true),
                      child: Text('Order ${picked.length}'),
                    )
                  : null,
            ),
            for (final s in suggested)
              CheckboxListTile(
                key: ValueKey('suggest-${s['item']}'),
                value: picked.contains(s['item']),
                onChanged: canManage
                    ? (v) => setState(() => v == true ? picked.add(s['item']) : picked.remove(s['item']))
                    : null,
                title: Text(s['name']),
                subtitle: Text(
                  'on hand ${stockQty(s['on_hand'], s['unit'])}'
                  '${_d(s['on_order']) > 0 ? ' · on order ${stockQty(s['on_order'], s['unit'])}' : ''}'
                  ' · up to ${stockQty(s['par_level'], s['unit'])}',
                ),
                secondary: Text(
                  '${packs(s, s['suggested_quantity'])}'
                  '${s['estimated_cost_minor'] != null ? '\n≈ ${money(s['estimated_cost_minor'])}' : ''}',
                  textAlign: TextAlign.right,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
          ],
          SectionLabel('Open orders (${open.length})'),
          if (open.isEmpty) const ListTile(title: Text('No open orders')),
          for (final o in open) tile(o),
          if (done.isNotEmpty) ...[const SectionLabel('Recent'), for (final o in done.take(20)) tile(o)],
        ],
      ),
    );
  }
}

/// New purchase order: supplier, due date, lines in purchase units.
class PurchaseOrderEditor extends StatefulWidget {
  const PurchaseOrderEditor({super.key, this.initialLines = const []});
  final List<Map<String, dynamic>> initialLines;

  @override
  State<PurchaseOrderEditor> createState() => _PurchaseOrderEditorState();
}

class _PurchaseOrderEditorState extends State<PurchaseOrderEditor> {
  List<Map<String, dynamic>> items = [];
  List<Map<String, dynamic>> suppliers = [];
  final supplier = TextEditingController();
  final note = TextEditingController();
  DateTime? due;
  final rows = <(String?, TextEditingController, int)>[];
  bool busy = false;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    for (final l in widget.initialLines) {
      rows.add((l['item'], TextEditingController(text: '${l['quantity']}'), (l['expected_cost_minor'] ?? 0) as int));
    }
    if (rows.isEmpty) rows.add((null, TextEditingController(), 0));
    _load();
  }

  Future<void> _load() async {
    try {
      final i = await app.api.get('stock');
      final s = await app.api.get('stock/suppliers');
      if (!mounted) return;
      setState(() {
        items = List<Map<String, dynamic>>.from(i.data['items']);
        suppliers = List<Map<String, dynamic>>.from(s.data['suppliers']);
      });
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Map<String, dynamic>? _item(String? code) => items.where((i) => i['code'] == code).firstOrNull;

  Future<void> _save({required bool send}) async {
    final lines = [
      for (final r in rows)
        if (r.$1 != null && r.$2.text.trim().isNotEmpty)
          {'item': r.$1, 'quantity': r.$2.text.trim(), 'expected_cost_minor': r.$3},
    ];
    if (lines.isEmpty) return showInfo(context, 'Add at least one item');
    setState(() => busy = true);
    final r = await runCorrection(context, app.api, 'stock/purchase-orders', {
      'supplier': supplier.text.trim(),
      'expected_on': due == null ? null : DateFormat('yyyy-MM-dd').format(due!),
      'note': note.text.trim(),
      'lines': lines,
      'send': send,
    });
    if (!mounted) return;
    setState(() => busy = false);
    if (r != null) Navigator.pop(context, r.data);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New purchase order'),
      content: SizedBox(
        width: 560,
        child: items.isEmpty
            ? const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()))
            : SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Autocomplete<String>(
                      optionsBuilder: (v) => suppliers
                          .map((s) => s['name'] as String)
                          .where((n) => n.toLowerCase().contains(v.text.toLowerCase())),
                      onSelected: (v) => supplier.text = v,
                      fieldViewBuilder: (ctx, ctl, focus, submit) {
                        ctl.addListener(() => supplier.text = ctl.text);
                        return TextField(
                          key: const ValueKey('po-supplier'),
                          controller: ctl,
                          focusNode: focus,
                          decoration: const InputDecoration(labelText: 'Supplier'),
                        );
                      },
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Text(due == null ? 'No due date' : 'Due ${DateFormat('EEE d MMM').format(due!)}'),
                        TextButton(
                          onPressed: () async {
                            final d = await showDatePicker(
                              context: context,
                              firstDate: DateTime.now(),
                              lastDate: DateTime.now().add(const Duration(days: 60)),
                              initialDate: DateTime.now().add(const Duration(days: 1)),
                            );
                            if (d != null) setState(() => due = d);
                          },
                          child: const Text('Pick date'),
                        ),
                      ],
                    ),
                    for (var i = 0; i < rows.length; i++)
                      Row(
                        children: [
                          Expanded(
                            flex: 3,
                            child: DropdownButtonFormField<String>(
                              key: ValueKey('po-item-$i'),
                              initialValue: rows[i].$1,
                              isExpanded: true,
                              hint: const Text('Item'),
                              items: [
                                for (final it in items)
                                  DropdownMenuItem(value: it['code'] as String, child: Text(it['name'])),
                              ],
                              onChanged: (v) => setState(() => rows[i] = (v, rows[i].$2, 0)),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            flex: 2,
                            child: TextField(
                              key: ValueKey('po-qty-$i'),
                              controller: rows[i].$2,
                              keyboardType: const TextInputType.numberWithOptions(decimal: true),
                              decoration: InputDecoration(
                                hintText: 'qty',
                                suffixText: () {
                                  final it = _item(rows[i].$1);
                                  if (it == null) return '';
                                  final pu = (it['purchase_unit'] ?? '').toString();
                                  return pu.isEmpty ? it['unit'] : '× $pu';
                                }(),
                              ),
                            ),
                          ),
                          IconButton(
                            tooltip: 'Remove line',
                            icon: const Icon(Icons.close),
                            onPressed: rows.length == 1 ? null : () => setState(() => rows.removeAt(i)),
                          ),
                        ],
                      ),
                    TextButton.icon(
                      onPressed: () => setState(() => rows.add((null, TextEditingController(), 0))),
                      icon: const Icon(Icons.add),
                      label: const Text('Add item'),
                    ),
                    TextField(
                      controller: note,
                      decoration: const InputDecoration(labelText: 'Note to supplier'),
                    ),
                  ],
                ),
              ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        OutlinedButton(
          key: const ValueKey('po-save-draft'),
          onPressed: busy ? null : () => _save(send: false),
          child: const Text('Save draft'),
        ),
        FilledButton(
          key: const ValueKey('po-save-send'),
          onPressed: busy ? null : () => _save(send: true),
          child: const Text('Create & mark sent'),
        ),
      ],
    );
  }
}

/// One purchase order: lines ordered/received, supplier message, send / receive / close / cancel.
class PurchaseOrderDetail extends StatefulWidget {
  const PurchaseOrderDetail({super.key, required this.orderId});
  final String orderId;

  @override
  State<PurchaseOrderDetail> createState() => _PurchaseOrderDetailState();
}

class _PurchaseOrderDetailState extends State<PurchaseOrderDetail> {
  Map<String, dynamic>? po;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('stock/purchase-orders/${widget.orderId}');
      if (mounted) setState(() => po = r.data);
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _action(String action) async {
    String? reason;
    if (action == 'cancel' || action == 'close') {
      reason = await askReason(
        context,
        action == 'cancel' ? 'Cancel ${po!['number']}' : 'Close ${po!['number']} short',
        quick: const ['Supplier out of stock', 'Ordered elsewhere', 'Not needed any more'],
      );
      if (reason == null) return;
    }
    if (!mounted) return;
    final r = await runCorrection(context, app.api, 'stock/purchase-orders/${po!['id']}', {
      'action': action,
      'expected_revision': po!['revision'],
      'reason': ?reason,
    });
    if (r != null && mounted) setState(() => po = r.data);
  }

  Future<void> _receive() async {
    final open = List<Map<String, dynamic>>.from(po!['lines']).where((l) => _d(l['outstanding']) > 0).toList();
    final got = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _ReceiveDialog(po: po!, lines: open),
    );
    if (got == null || !mounted) return;
    final r = await runCorrection(context, app.api, 'stock/purchase-orders/${po!['id']}/receive', {
      ...got,
      'expected_revision': po!['revision'],
    });
    if (r != null && mounted) {
      setState(() => po = r.data);
      showInfo(context, r.data['status'] == 'received' ? 'Order fully received' : 'Delivery booked; rest still due');
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = po;
    final t = Theme.of(context);
    if (p == null) {
      return const AlertDialog(
        content: SizedBox(height: 120, child: Center(child: CircularProgressIndicator())),
      );
    }
    final canManage = app.can('stock.manage');
    final status = p['status'] as String;
    return AlertDialog(
      title: Row(
        children: [
          Expanded(child: Text('${p['number']} · ${p['supplier']?['name'] ?? 'no supplier'}')),
          StatusChip(poStatusLabels[status] ?? status, color: poColor(t, status)),
        ],
      ),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                [
                  'by ${p['created_by']}',
                  if (p['expected_on'] != null) 'due ${p['expected_on']}',
                  if ((p['closed_reason'] ?? '').toString().isNotEmpty) '“${p['closed_reason']}”',
                ].join(' · '),
                style: t.textTheme.bodySmall,
              ),
              const SizedBox(height: 8),
              for (final l in List<Map<String, dynamic>>.from(p['lines']))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(l['name']),
                  subtitle: Text('received ${packs(l, l['received'])}'),
                  trailing: Text(packs(l, l['quantity']), style: const TextStyle(fontWeight: FontWeight.w700)),
                ),
              if ((p['receipts'] as List).isNotEmpty) ...[
                const SectionLabel('Deliveries'),
                for (final r in List<Map<String, dynamic>>.from(p['receipts']))
                  Text(
                    '${DateFormat('d MMM HH:mm').format(DateTime.parse(r['created_at']).toLocal())} · '
                    '${r['reference']} · ${r['staff']}${r['total_minor'] > 0 ? ' · ${money(r['total_minor'])}' : ''}',
                  ),
              ],
              const SectionLabel('Message for the supplier'),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: t.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SelectableText(p['message'] ?? '', key: const ValueKey('po-message')),
              ),
              TextButton.icon(
                key: const ValueKey('po-copy'),
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: p['message'] ?? ''));
                  if (context.mounted) showInfo(context, 'Copied — paste it into WhatsApp or SMS');
                },
                icon: const Icon(Icons.copy),
                label: Text(
                  'Copy${p['supplier']?['phone'] != null && p['supplier']['phone'] != '' ? ' (${p['supplier']['phone']})' : ''}',
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        if (canManage && (status == 'draft' || status == 'sent'))
          TextButton(onPressed: () => _action('cancel'), child: const Text('Cancel order')),
        if (canManage && status == 'partial')
          TextButton(onPressed: () => _action('close'), child: const Text('Close short')),
        if (canManage && status == 'draft')
          OutlinedButton(
            key: const ValueKey('po-send'),
            onPressed: () => _action('send'),
            child: const Text('Mark sent'),
          ),
        if (['draft', 'sent', 'partial'].contains(status))
          FilledButton(key: const ValueKey('po-receive'), onPressed: _receive, child: const Text('Receive delivery')),
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Close')),
      ],
    );
  }
}

class _ReceiveDialog extends StatefulWidget {
  const _ReceiveDialog({required this.po, required this.lines});
  final Map<String, dynamic> po;
  final List<Map<String, dynamic>> lines;

  @override
  State<_ReceiveDialog> createState() => _ReceiveDialogState();
}

class _ReceiveDialogState extends State<_ReceiveDialog> {
  late final qty = {
    for (final l in widget.lines)
      l['id']: TextEditingController(
        text: _n(_d(l['outstanding']) / (_d(l['purchase_factor']) > 0 ? _d(l['purchase_factor']) : 1)),
      ),
  };
  late final cost = {for (final l in widget.lines) l['id']: TextEditingController()};
  final ref = TextEditingController();

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Receive · ${widget.po['number']}'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('Enter what actually arrived. Anything not delivered stays on order.'),
              for (final l in widget.lines)
                Row(
                  children: [
                    Expanded(flex: 3, child: Text('${l['name']}\nstill due ${packs(l, l['outstanding'])}')),
                    const SizedBox(width: 8),
                    Expanded(
                      flex: 2,
                      child: TextField(
                        key: ValueKey('rcv-qty-${l['item']}'),
                        controller: qty[l['id']],
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: InputDecoration(
                          labelText: (l['purchase_unit'] ?? '').toString().isEmpty ? l['unit'] : l['purchase_unit'],
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      flex: 2,
                      child: TextField(
                        key: ValueKey('rcv-cost-${l['item']}'),
                        controller: cost[l['id']],
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: const InputDecoration(labelText: 'Cost (Rs)'),
                      ),
                    ),
                  ],
                ),
              TextField(
                key: const ValueKey('rcv-ref'),
                controller: ref,
                decoration: const InputDecoration(labelText: 'Supplier invoice / delivery note'),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('rcv-save'),
          onPressed: () => Navigator.pop(context, {
            'reference': ref.text.trim(),
            'lines': [
              for (final l in widget.lines)
                if (_d(qty[l['id']]!.text) > 0)
                  {
                    'line_id': l['id'],
                    'quantity': qty[l['id']]!.text.trim(),
                    'cost_minor': parseRupees(cost[l['id']]!.text) ?? 0,
                  },
            ],
          }),
          child: const Text('Book delivery'),
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------------------------------------ counts

class CountSheetsTab extends StatefulWidget {
  const CountSheetsTab({super.key});

  @override
  State<CountSheetsTab> createState() => _CountSheetsTabState();
}

class _CountSheetsTabState extends State<CountSheetsTab> {
  List<Map<String, dynamic>>? counts;
  Map<String, dynamic>? sheet;
  final ctl = <String, TextEditingController>{};
  String filter = '';
  bool busy = false;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('stock/count-sheets');
      final list = List<Map<String, dynamic>>.from(r.data['counts']);
      final open = list.where((c) => c['status'] == 'open' || c['status'] == 'submitted').firstOrNull;
      Map<String, dynamic>? s;
      if (open != null) s = (await app.api.get('stock/count-sheets/${open['id']}')).data;
      if (!mounted) return;
      setState(() {
        counts = list;
        _setSheet(s);
      });
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  void _setSheet(Map<String, dynamic>? s) {
    sheet = s;
    if (s == null) return;
    for (final l in List<Map<String, dynamic>>.from(s['lines'])) {
      final c = ctl.putIfAbsent(l['item'], () => TextEditingController());
      if (c.text.isEmpty && l['counted'] != null) c.text = _n(l['counted']);
    }
  }

  Future<void> _start() async {
    final r = await runCorrection(context, app.api, 'stock/count-sheets', {'note': 'Stock count'});
    if (r != null && mounted) {
      ctl.clear();
      setState(() => _setSheet(r.data));
      await _load();
    }
  }

  Future<void> _saveEntries() async {
    final s = sheet!;
    final lines = [
      for (final l in List<Map<String, dynamic>>.from(s['lines']))
        if (ctl[l['item']]!.text.trim().isNotEmpty && ctl[l['item']]!.text.trim() != _n(l['counted'] ?? ''))
          {'item': l['item'], 'counted': ctl[l['item']]!.text.trim()},
    ];
    if (lines.isEmpty) return;
    setState(() => busy = true);
    final r = await runCorrection(context, app.api, 'stock/count-sheets/${s['id']}/lines', {'lines': lines});
    if (!mounted) return;
    setState(() {
      busy = false;
      if (r != null) _setSheet(r.data);
    });
  }

  Future<void> _finish(String action) async {
    await _saveEntries();
    if (!mounted) return;
    if (action == 'cancel') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Cancel this count?'),
          content: const Text('Nothing will be adjusted.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Keep counting')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Cancel count')),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }
    final r = await runCorrection(context, app.api, 'stock/count-sheets/${sheet!['id']}', {
      'action': action,
      'expected_revision': sheet!['revision'],
    });
    if (r != null && mounted) {
      if (action == 'approve') {
        showInfo(context, 'Stock adjusted · variance ${money(r.data['variance_value_minor'])}');
      }
      ctl.clear();
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final list = counts;
    if (list == null) return const Center(child: CircularProgressIndicator());
    final t = Theme.of(context);
    final s = sheet;
    final manager = app.can('stock.adjust');
    if (s == null) {
      return ListView(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                const Expanded(
                  child: Text(
                    'Count everything on the shelves. Service can continue: each line is compared with the stock '
                    'the system expected at the moment you counted it.',
                  ),
                ),
                const SizedBox(width: 12),
                FilledButton.icon(
                  key: const ValueKey('count-start'),
                  onPressed: _start,
                  icon: const Icon(Icons.fact_check_outlined),
                  label: const Text('Start count'),
                ),
              ],
            ),
          ),
          const SectionLabel('Past counts'),
          for (final c in list)
            ListTile(
              title: Text('${c['number']} · ${c['created_by']}'),
              subtitle: Text(
                '${DateFormat('d MMM HH:mm').format(DateTime.parse(c['created_at']).toLocal())} · '
                '${c['counted']}/${c['total']} counted${c['approved_by'] != null ? ' · approved ${c['approved_by']}' : ''}',
              ),
              trailing: c['status'] == 'approved'
                  ? Text(
                      money(c['variance_value_minor']),
                      style: TextStyle(
                        fontWeight: FontWeight.w700,
                        color: (c['variance_value_minor'] as num) < 0 ? t.colorScheme.error : null,
                      ),
                    )
                  : StatusChip(c['status']),
            ),
        ],
      );
    }
    final lines = List<Map<String, dynamic>>.from(
      s['lines'],
    ).where((l) => filter.isEmpty || l['name'].toString().toLowerCase().contains(filter.toLowerCase())).toList();
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  '${s['number']} · ${s['counted']}/${s['total']} counted'
                  '${manager ? ' · variance so far ${money(s['variance_value_minor'])}' : ''}',
                  style: t.textTheme.titleMedium,
                ),
              ),
              SizedBox(
                width: 200,
                child: TextField(
                  decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Find', isDense: true),
                  onChanged: (v) => setState(() => filter = v),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ListView(
            children: [
              for (final l in lines)
                ListTile(
                  key: ValueKey('count-${l['item']}'),
                  title: Text(l['name']),
                  subtitle: l['counted'] == null
                      ? const Text('not counted yet')
                      : Text(
                          manager
                              ? 'expected ${stockQty(l['expected'], l['unit'])} · '
                                    'variance ${_d(l['variance']) > 0 ? '+' : ''}${stockQty(l['variance'], l['unit'])}'
                                    '${l['variance_value_minor'] != null ? ' (${money(l['variance_value_minor'])})' : ''}'
                              : 'counted by ${l['counted_by']}',
                          style: TextStyle(color: manager && _d(l['variance']) != 0 ? Colors.orange.shade800 : null),
                        ),
                  trailing: SizedBox(
                    width: 150,
                    child: TextField(
                      key: ValueKey('count-input-${l['item']}'),
                      controller: ctl[l['item']],
                      enabled: s['status'] == 'open',
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      textAlign: TextAlign.right,
                      decoration: InputDecoration(suffixText: l['unit'], isDense: true),
                      onSubmitted: (_) => _saveEntries(),
                    ),
                  ),
                ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(12),
          child: Wrap(
            spacing: 8,
            alignment: WrapAlignment.end,
            children: [
              TextButton(onPressed: () => _finish('cancel'), child: const Text('Cancel count')),
              if (s['status'] == 'open')
                OutlinedButton(
                  key: const ValueKey('count-save'),
                  onPressed: busy ? null : _saveEntries,
                  child: const Text('Save counts'),
                ),
              if (s['status'] == 'open' && !manager)
                FilledButton(onPressed: () => _finish('submit'), child: const Text('Submit for approval')),
              FilledButton(
                key: const ValueKey('count-approve'),
                onPressed: busy ? null : () => _finish('approve'),
                child: const Text('Approve & adjust stock'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
