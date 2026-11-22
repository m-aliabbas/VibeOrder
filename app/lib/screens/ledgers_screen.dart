import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

/// Ledgers: cash, sales, supplier, customer and stock. Same data on screen and on the A4 print page.
class LedgersScreen extends StatefulWidget {
  const LedgersScreen({super.key});

  @override
  State<LedgersScreen> createState() => _LedgersScreenState();
}

const ledgerKinds = [
  ('cash', 'Cash', Icons.payments_outlined),
  ('sales', 'Sales', Icons.receipt_long_outlined),
  ('supplier', 'Suppliers', Icons.local_shipping_outlined),
  ('customer', 'Customers', Icons.people_outline),
  ('stock', 'Stock', Icons.inventory_2_outlined),
];

class _LedgersScreenState extends State<LedgersScreen> {
  String kind = 'cash';
  late DateTimeRange range;
  Map<String, dynamic>? data;
  String? error;
  bool loading = false;
  // Drill-down filters (supplier / customer / item) with a label for the chip.
  (String, String, String)? filter;
  List<Map<String, dynamic>> suppliers = [];

  AppState get app => context.read<AppState>();
  static final _d = DateFormat('yyyy-MM-dd');

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    range = DateTimeRange(start: today.subtract(const Duration(days: 6)), end: today);
    _load();
  }

  Future<void> _load() async {
    setState(() => loading = true);
    try {
      final r = await app.api.get(
        'ledgers/$kind',
        query: {'from': _d.format(range.start), 'to': _d.format(range.end), if (filter != null) filter!.$1: filter!.$2},
      );
      if (kind == 'supplier' && suppliers.isEmpty) {
        final s = await app.api.get('stock/suppliers');
        suppliers = List<Map<String, dynamic>>.from(s.data['suppliers']);
      }
      if (mounted) {
        setState(() {
          data = r.data;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  void _setKind(String k) {
    setState(() {
      kind = k;
      filter = null;
      data = null;
    });
    _load();
  }

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final r = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 2),
      lastDate: DateTime(now.year, now.month, now.day),
      initialDateRange: range,
    );
    if (r != null) {
      setState(() => range = r);
      _load();
    }
  }

  void _quick(int days) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    setState(
      () => range = DateTimeRange(
        start: today.subtract(Duration(days: days - 1)),
        end: today,
      ),
    );
    _load();
  }

  Future<void> _print() async {
    final path = data?['print_path'];
    if (path == null) return;
    final base = app.server.endsWith('/') ? app.server.substring(0, app.server.length - 1) : app.server;
    final ok = await launchUrl(Uri.parse('$base$path?print=1'), webOnlyWindowName: '_blank');
    if (!ok && mounted) showInfo(context, 'Open this link to print: $base$path');
  }

  /// Tap a summary row to open that supplier / customer / item's own ledger.
  void _drill(Map<String, dynamic> row) {
    final id = row['id'];
    if (id == null) return;
    final (key, label) = switch (kind) {
      'customer' => ('customer', row['customer'] ?? row['phone'] ?? ''),
      'supplier' => ('supplier', row['supplier'] ?? ''),
      'stock' => ('item', row['item'] ?? ''),
      _ => ('', ''),
    };
    if (key.isEmpty) return;
    setState(() => filter = (key, id as String, label as String));
    _load();
  }

  Future<void> _paySupplier() async {
    final paid = await showDialog<bool>(
      context: context,
      builder: (_) =>
          _PaySupplierDialog(suppliers: suppliers, preselected: filter?.$1 == 'supplier' ? filter!.$2 : null),
    );
    if (paid == true) {
      suppliers = [];
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final f = DateFormat('d MMM yyyy');
    final d = data;
    return Scaffold(
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            Row(
              children: [
                Expanded(child: Text('Ledgers', style: t.textTheme.headlineSmall)),
                if (kind == 'supplier' && app.can('supplier.pay'))
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: OutlinedButton.icon(
                      key: const ValueKey('pay-supplier'),
                      onPressed: _paySupplier,
                      icon: const Icon(Icons.outbox_outlined),
                      label: const Text('Pay supplier'),
                    ),
                  ),
                FilledButton.icon(
                  key: const ValueKey('print-a4'),
                  onPressed: d == null ? null : _print,
                  icon: const Icon(Icons.print_outlined),
                  label: const Text('Print A4'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SegmentedButton<String>(
                key: const ValueKey('ledger-kind'),
                showSelectedIcon: false,
                segments: [
                  for (final k in ledgerKinds)
                    ButtonSegment(value: k.$1, label: Text(k.$2), icon: Icon(k.$3, size: 18)),
                ],
                selected: {kind},
                onSelectionChanged: (s) => _setKind(s.first),
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                ActionChip(
                  avatar: const Icon(Icons.date_range, size: 18),
                  label: Text(
                    range.start == range.end
                        ? f.format(range.start)
                        : '${f.format(range.start)} – ${f.format(range.end)}',
                  ),
                  onPressed: _pickRange,
                ),
                for (final q in const [(1, 'Today'), (7, '7 days'), (30, '30 days')])
                  ChoiceChip(
                    label: Text(q.$2),
                    selected:
                        range.end.difference(range.start).inDays + 1 == q.$1 &&
                        DateUtils.isSameDay(range.end, DateTime.now()),
                    onSelected: (_) => _quick(q.$1),
                  ),
                if (kind == 'supplier' && suppliers.isNotEmpty)
                  DropdownButton<String?>(
                    key: const ValueKey('supplier-filter'),
                    value: filter?.$1 == 'supplier' ? filter!.$2 : null,
                    hint: const Text('All suppliers'),
                    onChanged: (v) {
                      setState(() {
                        filter = v == null
                            ? null
                            : ('supplier', v, suppliers.firstWhere((s) => s['id'] == v)['name'] as String);
                      });
                      _load();
                    },
                    items: [
                      const DropdownMenuItem(value: null, child: Text('All suppliers')),
                      for (final s in suppliers)
                        DropdownMenuItem(
                          value: s['id'] as String,
                          child: Text('${s['name']} · owed ${money(s['balance_minor'])}'),
                        ),
                    ],
                  ),
                if (filter != null && kind != 'supplier')
                  InputChip(
                    label: Text(filter!.$3),
                    onDeleted: () {
                      setState(() => filter = null);
                      _load();
                    },
                  ),
                if (loading) const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
              ],
            ),
            const SizedBox(height: 14),
            if (error != null)
              Card(
                child: ListTile(
                  leading: Icon(Icons.error_outline, color: t.colorScheme.error),
                  title: Text(error!),
                ),
              ),
            if (d != null) ...[
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  for (final s in d['summary'])
                    Container(
                      constraints: const BoxConstraints(minWidth: 140),
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      decoration: BoxDecoration(
                        color: t.colorScheme.surfaceContainerLowest,
                        border: Border.all(color: t.colorScheme.outlineVariant),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            s['label'],
                            style: t.textTheme.labelMedium?.copyWith(color: t.colorScheme.onSurfaceVariant),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            s['money'] == true ? money(s['value']) : '${s['value']}',
                            style: t.textTheme.titleMedium,
                          ),
                        ],
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 16),
              if ((d['sections'] as List).isEmpty)
                const Padding(
                  padding: EdgeInsets.all(40),
                  child: Center(child: Text('No entries in this period')),
                ),
              for (final sec in d['sections'])
                _SectionTable(sec: Map<String, dynamic>.from(sec), onRow: _drill, drillable: filter == null),
            ],
          ],
        ),
      ),
    );
  }
}

class _SectionTable extends StatelessWidget {
  const _SectionTable({required this.sec, required this.onRow, required this.drillable});
  final Map<String, dynamic> sec;
  final void Function(Map<String, dynamic>) onRow;
  final bool drillable;

  String _cell(dynamic v, Map c) {
    if (v == null) return '';
    if (c['money'] == true) return money(v as num);
    return '$v';
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final cols = List<Map<String, dynamic>>.from(sec['columns']);
    final rows = List<Map<String, dynamic>>.from(sec['rows']);
    final totals = Map<String, dynamic>.from(sec['totals'] ?? const {});
    final shown = rows.take(500).toList();
    const figures = TextStyle(fontFeatures: [FontFeature.tabularFigures()]);
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Card(
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(sec['title'] ?? '', style: t.textTheme.titleMedium),
                  if ((sec['subtitle'] ?? '').toString().isNotEmpty)
                    Text(
                      sec['subtitle'],
                      style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
                    ),
                ],
              ),
            ),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: DataTable(
                headingRowHeight: 40,
                dataRowMinHeight: 40,
                dataRowMaxHeight: 52,
                columnSpacing: 22,
                headingRowColor: WidgetStatePropertyAll(t.colorScheme.surfaceContainer),
                showCheckboxColumn: false,
                columns: [
                  for (final c in cols)
                    DataColumn(
                      numeric: c['align'] == 'right',
                      label: Text(c['label'], style: const TextStyle(fontWeight: FontWeight.w800)),
                    ),
                ],
                rows: [
                  for (final r in shown)
                    DataRow(
                      onSelectChanged: drillable && r['id'] != null ? (_) => onRow(r) : null,
                      cells: [
                        for (final c in cols)
                          DataCell(
                            Text(
                              _cell(r[c['key']], c),
                              style: c['money'] == true && (r[c['key']] ?? 0) is num && (r[c['key']] ?? 0) < 0
                                  ? figures.copyWith(color: t.colorScheme.error)
                                  : figures,
                            ),
                          ),
                      ],
                    ),
                  if (totals.isNotEmpty)
                    DataRow(
                      color: WidgetStatePropertyAll(t.colorScheme.surfaceContainerLow),
                      cells: [
                        for (final c in cols)
                          DataCell(
                            Text(_cell(totals[c['key']], c), style: figures.copyWith(fontWeight: FontWeight.w800)),
                          ),
                      ],
                    ),
                ],
              ),
            ),
            if (rows.length > shown.length)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Text(
                  'Showing ${shown.length} of ${rows.length} rows. Print A4 for all of them.',
                  style: t.textTheme.bodySmall,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _PaySupplierDialog extends StatefulWidget {
  const _PaySupplierDialog({required this.suppliers, this.preselected});
  final List<Map<String, dynamic>> suppliers;
  final String? preselected;

  @override
  State<_PaySupplierDialog> createState() => _PaySupplierDialogState();
}

class _PaySupplierDialogState extends State<_PaySupplierDialog> {
  String? supplier;
  String method = 'cash';
  final amount = TextEditingController();
  final reference = TextEditingController();
  final note = TextEditingController();
  final key = ApiClient.newKey();
  bool busy = false;
  String? error;

  @override
  void initState() {
    super.initState();
    supplier = widget.preselected;
  }

  Future<void> _save() async {
    final minor = parseRupees(amount.text);
    if (supplier == null || minor == null || minor <= 0) {
      setState(() => error = 'Choose the supplier and enter the amount');
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await context.read<AppState>().api.command('stock/suppliers/$supplier/payments', {
        'amount_minor': minor,
        'method': method,
        'reference': reference.text.trim(),
        'note': note.text.trim(),
      }, key: key);
      if (mounted) {
        Navigator.pop(context, true);
        showInfo(context, 'Paid ${money(minor)}. Still owed ${money(r.data['balance_minor'])}');
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final owed = widget.suppliers.where((s) => s['id'] == supplier).map((s) => s['balance_minor']).firstOrNull;
    return AlertDialog(
      title: const Text('Pay supplier'),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: supplier,
              decoration: const InputDecoration(labelText: 'Supplier'),
              items: [
                for (final s in widget.suppliers)
                  DropdownMenuItem(value: s['id'] as String, child: Text(s['name'] as String)),
              ],
              onChanged: (v) => setState(() => supplier = v),
            ),
            if (owed != null)
              Padding(padding: const EdgeInsets.only(top: 6), child: Text('Currently owed: ${money(owed)}')),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('pay-amount'),
              controller: amount,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: const InputDecoration(labelText: 'Amount (Rs)'),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final m in const [
                  ('cash', 'Cash from drawer'),
                  ('bank', 'Bank transfer'),
                  ('wallet', 'JazzCash / EasyPaisa'),
                  ('cheque', 'Cheque'),
                ])
                  ChoiceChip(
                    label: Text(m.$2),
                    selected: method == m.$1,
                    onSelected: (_) => setState(() => method = m.$1),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              controller: reference,
              decoration: const InputDecoration(labelText: 'Reference (cheque / transaction no.)'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: note,
              decoration: const InputDecoration(labelText: 'Note'),
            ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(onPressed: busy ? null : _save, child: const Text('Record payment')),
      ],
    );
  }
}
