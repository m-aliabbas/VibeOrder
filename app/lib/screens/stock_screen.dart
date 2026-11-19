import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';
import 'stock_purchasing.dart';

/// Stock (manager): levels with reorder alerts, item master, versioned recipes with cost, and branch sold-out
/// switches. Every write goes through a command with an idempotency key.
class StockScreen extends StatelessWidget {
  const StockScreen({super.key, this.initialTab = 0});
  final int initialTab;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final tabs = <(String, Widget)>[
      ('Levels', const StockLevelsTab()),
      ('Recipes', const RecipesTab()),
      ('Orders', const PurchaseOrdersTab()),
      ('Counts', const CountSheetsTab()),
      if (app.can('availability.set')) ('Sold out', const SoldOutTab()),
    ];
    return DefaultTabController(
      length: tabs.length,
      initialIndex: initialTab.clamp(0, tabs.length - 1),
      child: Column(
        children: [
          Material(
            color: Theme.of(context).colorScheme.surface,
            child: TabBar(
              tabs: [for (final t in tabs) Tab(key: ValueKey('tab-${t.$1}'), text: t.$1)],
            ),
          ),
          Expanded(child: TabBarView(children: [for (final t in tabs) t.$2])),
        ],
      ),
    );
  }
}

String stockQty(dynamic v, String unit) {
  final d = double.tryParse(v.toString()) ?? 0;
  if (unit == 'g' && d.abs() >= 1000) return '${(d / 1000).toStringAsFixed(2)} kg';
  if (unit == 'ml' && d.abs() >= 1000) return '${(d / 1000).toStringAsFixed(2)} L';
  return '${d == d.roundToDouble() ? d.toInt() : d.toStringAsFixed(1)} $unit';
}

String _num(dynamic v) {
  final d = double.tryParse(v?.toString() ?? '');
  if (d == null) return '';
  return d == d.roundToDouble() ? d.toInt().toString() : d.toString();
}

Color statusColor(ThemeData t, String? status) => switch (status) {
  'out' => t.colorScheme.error,
  'low' => Colors.orange.shade700,
  _ => Colors.green.shade600,
};

// ------------------------------------------------------------------------------------------------ levels

class StockLevelsTab extends StatefulWidget {
  const StockLevelsTab({super.key});

  @override
  State<StockLevelsTab> createState() => _StockLevelsTabState();
}

class _StockLevelsTabState extends State<StockLevelsTab> {
  List<Map<String, dynamic>> items = [];
  String filter = '';
  bool onlyAttention = false;
  bool loading = true;

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
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _action(Map<String, dynamic> item, String kind) async {
    if (kind == 'edit') return _editItem(item);
    if (kind == 'history') return _history(item);
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
                  key: const ValueKey('stock-cost'),
                  controller: cost,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(labelText: 'Total cost (Rs)'),
                ),
              TextField(
                key: const ValueKey('stock-note'),
                controller: note,
                decoration: InputDecoration(labelText: purchase ? 'Supplier invoice' : 'Reason'),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(
            key: const ValueKey('stock-save'),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Save'),
          ),
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

  Future<void> _editItem(Map<String, dynamic>? item) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => StockItemDialog(item: item),
    );
    if (saved == true) await _load();
  }

  Future<void> _history(Map<String, dynamic> item) async {
    List<Map<String, dynamic>> rows;
    try {
      rows = List<Map<String, dynamic>>.from(
        (await app.api.get('stock/movements', query: {'item': item['id']})).data['movements'],
      );
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
      return;
    }
    if (!mounted) return;
    const kinds = {
      'consumption': 'Used in kitchen',
      'packaging': 'Packaging',
      'reversal': 'Reversed',
      'loss': 'Loss',
      'receipt': 'Received',
      'transfer_out': 'Transfer out',
      'transfer_in': 'Transfer in',
      'count': 'Count adjustment',
      'return': 'Returned',
    };
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: SizedBox(
          height: MediaQuery.sizeOf(ctx).height * 0.7,
          child: Column(
            children: [
              ListTile(title: Text('${item['name']} · history', style: Theme.of(ctx).textTheme.titleLarge)),
              Expanded(
                child: rows.isEmpty
                    ? const Center(child: Text('No movements yet'))
                    : ListView(
                        children: [
                          for (final m in rows)
                            ListTile(
                              dense: true,
                              title: Text(kinds[m['kind']] ?? m['kind']),
                              subtitle: Text(
                                [
                                  DateFormat('d MMM HH:mm').format(DateTime.parse(m['created_at']).toLocal()),
                                  if (m['staff'] != null) m['staff'],
                                  if ((m['reason'] ?? '').toString().isNotEmpty) m['reason'],
                                  if (m['approved_by'] != null) 'approved ${m['approved_by']}',
                                ].join(' · '),
                              ),
                              trailing: Text(
                                '${(double.tryParse(m['quantity'].toString()) ?? 0) > 0 ? '+' : ''}'
                                '${stockQty(m['quantity'], m['unit'])}',
                                style: const TextStyle(fontWeight: FontWeight.w700),
                              ),
                            ),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final canManage = app.can('stock.manage');
    final attention = items.where((i) => i['status'] != 'ok').length;
    final shown = items
        .where((i) => filter.isEmpty || i['name'].toString().toLowerCase().contains(filter.toLowerCase()))
        .where((i) => !onlyAttention || i['status'] != 'ok')
        .toList();
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search),
                    hintText: 'Find item',
                    isDense: true,
                  ),
                  onChanged: (v) => setState(() => filter = v),
                ),
              ),
              const SizedBox(width: 8),
              FilterChip(
                key: const ValueKey('stock-attention'),
                label: Text('Low / out ($attention)'),
                selected: onlyAttention,
                onSelected: (v) => setState(() => onlyAttention = v),
              ),
              if (canManage) ...[
                const SizedBox(width: 8),
                FilledButton.tonalIcon(
                  key: const ValueKey('stock-add-item'),
                  onPressed: () => _editItem(null),
                  icon: const Icon(Icons.add),
                  label: const Text('Item'),
                ),
              ],
            ],
          ),
        ),
        Expanded(
          child: loading
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    children: [
                      for (final i in shown)
                        ListTile(
                          key: ValueKey('stock-${i['code']}'),
                          minTileHeight: 56,
                          leading: Icon(
                            i['status'] == 'ok' ? Icons.check_circle_outline : Icons.warning_amber_rounded,
                            color: statusColor(t, i['status']),
                          ),
                          title: Text(i['name']),
                          subtitle: Text(
                            [
                              i['cost_missing'] == true
                                  ? 'cost unknown'
                                  : 'avg ${money(double.parse(i['avg_cost_minor'].toString()))} per ${i['unit']}',
                              if (i['reorder_level'] != null) 'reorder at ${stockQty(i['reorder_level'], i['unit'])}',
                            ].join(' · '),
                          ),
                          onTap: () => _history(i),
                          trailing: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (i['status'] != 'ok')
                                Padding(
                                  padding: const EdgeInsets.only(right: 8),
                                  child: StatusChip(
                                    i['status'] == 'out' ? 'OUT' : 'LOW',
                                    color: statusColor(t, i['status']),
                                  ),
                                ),
                              Text(
                                stockQty(i['on_hand'], i['unit']),
                                style: t.textTheme.titleMedium?.copyWith(
                                  fontWeight: FontWeight.w800,
                                  color: i['status'] == 'ok' ? null : statusColor(t, i['status']),
                                ),
                              ),
                              PopupMenuButton<String>(
                                key: ValueKey('stock-menu-${i['code']}'),
                                onSelected: (k) => _action(i, k),
                                itemBuilder: (_) => [
                                  const PopupMenuItem(value: 'receipts', child: Text('Receive purchase')),
                                  const PopupMenuItem(value: 'losses', child: Text('Record loss')),
                                  const PopupMenuItem(value: 'counts', child: Text('Count')),
                                  const PopupMenuItem(value: 'history', child: Text('History')),
                                  if (canManage)
                                    const PopupMenuItem(value: 'edit', child: Text('Edit item / reorder level')),
                                ],
                              ),
                            ],
                          ),
                        ),
                      if (shown.isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(32),
                          child: Center(child: Text('Nothing here')),
                        ),
                    ],
                  ),
                ),
        ),
      ],
    );
  }
}

/// Create or edit a stock item. The reorder level applies to this branch.
class StockItemDialog extends StatefulWidget {
  const StockItemDialog({super.key, this.item});
  final Map<String, dynamic>? item;

  @override
  State<StockItemDialog> createState() => _StockItemDialogState();
}

class _StockItemDialogState extends State<StockItemDialog> {
  late final code = TextEditingController();
  late final name = TextEditingController(text: widget.item?['name']);
  late final pu = TextEditingController(text: widget.item?['purchase_unit']);
  late final factor = TextEditingController(text: _num(widget.item?['purchase_factor'] ?? 1));
  late final reorder = TextEditingController(text: _num(widget.item?['reorder_level']));
  late String unit = widget.item?['unit'] ?? 'g';
  bool busy = false;

  Future<void> _save() async {
    final app = context.read<AppState>();
    final body = <String, dynamic>{
      'name': name.text.trim(),
      'base_unit': unit,
      'purchase_unit': pu.text.trim(),
      'purchase_factor': factor.text.trim().isEmpty ? '1' : factor.text.trim(),
      'reorder_level': reorder.text.trim().isEmpty ? null : reorder.text.trim(),
    };
    final item = widget.item;
    if (item == null) {
      body['code'] = code.text.trim().isEmpty ? name.text.trim() : code.text.trim();
    } else {
      body['expected_revision'] = item['revision'];
    }
    setState(() => busy = true);
    final r = await runCorrection(context, app.api, item == null ? 'stock/items' : 'stock/items/${item['id']}', body);
    if (!mounted) return;
    setState(() => busy = false);
    if (r != null) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final editing = widget.item != null;
    return AlertDialog(
      title: Text(editing ? 'Edit ${widget.item!['name']}' : 'New stock item'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                key: const ValueKey('item-name'),
                controller: name,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'Name'),
              ),
              if (!editing)
                TextField(
                  key: const ValueKey('item-code'),
                  controller: code,
                  decoration: const InputDecoration(labelText: 'Code (optional, e.g. garlic-sauce)'),
                ),
              const SizedBox(height: 12),
              Row(
                children: [
                  const Text('Base unit'),
                  const SizedBox(width: 12),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'g', label: Text('grams')),
                      ButtonSegment(value: 'ml', label: Text('ml')),
                      ButtonSegment(value: 'pcs', label: Text('pieces')),
                    ],
                    selected: {unit},
                    onSelectionChanged: (s) => setState(() => unit = s.first),
                  ),
                ],
              ),
              TextField(
                controller: pu,
                decoration: const InputDecoration(labelText: 'Purchase unit (e.g. 5 kg bag)'),
              ),
              TextField(
                controller: factor,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(labelText: '$unit per purchase unit'),
              ),
              TextField(
                key: const ValueKey('item-reorder'),
                controller: reorder,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                  labelText: 'Alert when at or below ($unit)',
                  helperText: 'Leave empty for no low-stock alert',
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('item-save'),
          onPressed: busy ? null : _save,
          child: Text(editing ? 'Save' : 'Create'),
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------------------------------------ recipes

const recipeKindLabels = {
  'base': 'Pizza base',
  'product': 'Menu item',
  'option': 'Topping / option',
  'packaging': 'Packaging',
};

String sizeLabel(String? code) => (code == null || code.isEmpty) ? 'any size' : code;

class RecipesTab extends StatefulWidget {
  const RecipesTab({super.key});

  @override
  State<RecipesTab> createState() => _RecipesTabState();
}

class _RecipesTabState extends State<RecipesTab> {
  Map<String, dynamic>? data;
  String filter = '';

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('stock/recipes');
      if (mounted) setState(() => data = r.data);
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _edit({Map<String, dynamic>? recipe, Map<String, dynamic>? target}) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => RecipeEditor(
        recipe: recipe,
        target: target,
        items: List<Map<String, dynamic>>.from(data!['items']),
        targets: List<Map<String, dynamic>>.from(data!['targets']),
      ),
    );
    if (saved == true) await _load();
  }

  Future<void> _history(Map<String, dynamic> r) async {
    List<Map<String, dynamic>> versions;
    try {
      versions = List<Map<String, dynamic>>.from(
        (await app.api.get(
          'stock/recipes',
          query: {'target_kind': r['target_kind'], 'target_code': r['target_code'], 'size_code': r['size_code']},
        )).data['versions'],
      );
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
      return;
    }
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: SizedBox(
          height: MediaQuery.sizeOf(ctx).height * 0.7,
          child: ListView(
            children: [
              ListTile(
                title: Text('${r['label']} · ${sizeLabel(r['size_code'])}', style: Theme.of(ctx).textTheme.titleLarge),
                subtitle: const Text('Every version is kept; past orders keep the recipe they used.'),
              ),
              for (final v in versions)
                ListTile(
                  title: Text('v${v['version']}${v['active'] == true ? ' · current' : ''} · ${money(v['cost_minor'])}'),
                  subtitle: Text(
                    [
                      (v['components'] as List)
                          .map((c) => '${c['name']} ${stockQty(c['quantity'], c['unit'])}')
                          .join(', '),
                      DateFormat('d MMM yyyy HH:mm').format(DateTime.parse(v['created_at']).toLocal()),
                      if (v['created_by'] != null) v['created_by'],
                      if ((v['note'] ?? '').toString().isNotEmpty) '“${v['note']}”',
                    ].join(' · '),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final d = data;
    if (d == null) return const Center(child: CircularProgressIndicator());
    final t = Theme.of(context);
    final canManage = app.can('stock.manage');
    bool match(Map r) => filter.isEmpty || r['label'].toString().toLowerCase().contains(filter.toLowerCase());
    final recipes = List<Map<String, dynamic>>.from(d['recipes']).where(match).toList();
    final missing = List<Map<String, dynamic>>.from(d['missing']).where(match).toList();
    final groups = <String, List<Map<String, dynamic>>>{};
    for (final r in recipes) {
      groups.putIfAbsent(r['target_kind'], () => []).add(r);
    }
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search),
                    hintText: 'Find recipe',
                    isDense: true,
                  ),
                  onChanged: (v) => setState(() => filter = v),
                ),
              ),
              if (canManage) ...[
                const SizedBox(width: 8),
                FilledButton.tonalIcon(
                  key: const ValueKey('recipe-new'),
                  onPressed: () => _edit(),
                  icon: const Icon(Icons.add),
                  label: const Text('Recipe'),
                ),
              ],
            ],
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              children: [
                if (missing.isNotEmpty) ...[
                  SectionLabel('Missing recipes (${missing.length}) — no stock used, margin unknown'),
                  for (final m in missing)
                    ListTile(
                      key: ValueKey('missing-${m['target_code']}-${m['size_code']}'),
                      leading: Icon(Icons.report_gmailerrorred, color: Colors.orange.shade700),
                      title: Text(m['label']),
                      subtitle: Text(recipeKindLabels[m['target_kind']] ?? m['target_kind']),
                      trailing: canManage ? const Icon(Icons.add) : null,
                      onTap: canManage ? () => _edit(target: m) : null,
                    ),
                ],
                for (final kind in const ['base', 'product', 'option', 'packaging'])
                  if (groups[kind] != null) ...[
                    SectionLabel(recipeKindLabels[kind]!),
                    for (final r in groups[kind]!)
                      ListTile(
                        key: ValueKey('recipe-${r['target_kind']}-${r['target_code']}-${r['size_code']}'),
                        title: Text('${r['label']} · ${sizeLabel(r['size_code'])}'),
                        subtitle: Text(
                          (r['components'] as List)
                              .map((c) => '${c['name']} ${stockQty(c['quantity'], c['unit'])}')
                              .join(', '),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              crossAxisAlignment: CrossAxisAlignment.end,
                              children: [
                                Text(
                                  r['cost_missing'] == true ? 'cost ?' : money(r['cost_minor']),
                                  style: t.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700),
                                ),
                                Text('v${r['version']}', style: t.textTheme.labelSmall),
                              ],
                            ),
                            IconButton(
                              tooltip: 'Versions',
                              icon: const Icon(Icons.history),
                              onPressed: () => _history(r),
                            ),
                          ],
                        ),
                        onTap: canManage ? () => _edit(recipe: r) : () => _history(r),
                      ),
                  ],
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// Publishes a new recipe version (the current one is retired, never edited).
class RecipeEditor extends StatefulWidget {
  const RecipeEditor({super.key, this.recipe, this.target, required this.items, required this.targets});
  final Map<String, dynamic>? recipe;
  final Map<String, dynamic>? target;
  final List<Map<String, dynamic>> items;
  final List<Map<String, dynamic>> targets;

  @override
  State<RecipeEditor> createState() => _RecipeEditorState();
}

class _RecipeEditorState extends State<RecipeEditor> {
  late Map<String, dynamic>? target;
  String? size;
  final rows = <(String?, TextEditingController)>[];
  final note = TextEditingController();
  bool busy = false;

  bool get fixedTarget => widget.recipe != null || widget.target != null;

  @override
  void initState() {
    super.initState();
    final src = widget.recipe ?? widget.target;
    if (src != null) {
      target = widget.targets.firstWhere(
        (t) => t['target_kind'] == src['target_kind'] && t['target_code'] == src['target_code'],
        orElse: () => {
          'target_kind': src['target_kind'],
          'target_code': src['target_code'],
          'label': src['label'] ?? src['target_code'],
          'sizes': [
            {'code': src['size_code'] ?? '', 'name': src['size_code'] ?? ''},
          ],
        },
      );
      size = src['size_code'] ?? '';
    } else {
      target = null;
    }
    for (final c in (widget.recipe?['components'] as List?) ?? const []) {
      rows.add((c['item'] as String, TextEditingController(text: _num(c['quantity']))));
    }
    if (rows.isEmpty) rows.add((null, TextEditingController()));
  }

  String _unit(String? code) => widget.items.firstWhere((i) => i['code'] == code, orElse: () => {'unit': ''})['unit'];

  Future<void> _publish({bool retire = false}) async {
    final t = target;
    if (t == null || size == null) return showInfo(context, 'Choose what this recipe is for');
    final comps = [
      for (final r in rows)
        if (r.$1 != null && r.$2.text.trim().isNotEmpty) {'item': r.$1, 'quantity': r.$2.text.trim()},
    ];
    if (!retire && comps.isEmpty) return showInfo(context, 'Add at least one ingredient');
    setState(() => busy = true);
    final res = await runCorrection(context, context.read<AppState>().api, 'stock/recipes', {
      'target_kind': t['target_kind'],
      'target_code': t['target_code'],
      'size_code': size,
      'components': retire ? [] : comps,
      'note': note.text.trim(),
      'expected_version': widget.recipe?['version'] ?? 0,
    });
    if (!mounted) return;
    setState(() => busy = false);
    if (res != null) {
      showInfo(context, retire ? 'Recipe retired' : 'Recipe v${res.data['recipe']['version']} is now in use');
      Navigator.pop(context, true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = target;
    final sizes = t == null ? const [] : List<Map<String, dynamic>>.from(t['sizes']);
    return AlertDialog(
      title: Text(
        widget.recipe == null
            ? 'New recipe'
            : 'Change recipe · v${widget.recipe!['version']} → v${widget.recipe!['version'] + 1}',
      ),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (fixedTarget)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('${t!['label']} · ${sizeLabel(size)}'),
                  subtitle: Text(recipeKindLabels[t['target_kind']] ?? t['target_kind']),
                )
              else ...[
                DropdownButtonFormField<Map<String, dynamic>>(
                  key: const ValueKey('recipe-target'),
                  initialValue: t,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Recipe for'),
                  items: [
                    for (final x in widget.targets)
                      DropdownMenuItem(
                        value: x,
                        child: Text('${x['label']} (${recipeKindLabels[x['target_kind']] ?? x['target_kind']})'),
                      ),
                  ],
                  onChanged: (v) => setState(() {
                    target = v;
                    final s = List<Map<String, dynamic>>.from(v?['sizes'] ?? const []);
                    size = s.isEmpty ? '' : s.first['code'];
                  }),
                ),
                if (sizes.length > 1)
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final s in sizes)
                        ChoiceChip(
                          label: Text((s['name'] ?? '').toString().isEmpty ? sizeLabel(s['code']) : s['name']),
                          selected: size == s['code'],
                          onSelected: (_) => setState(() => size = s['code']),
                        ),
                    ],
                  ),
              ],
              const SizedBox(height: 8),
              Text('Ingredients per portion', style: Theme.of(context).textTheme.titleSmall),
              for (var i = 0; i < rows.length; i++)
                Row(
                  children: [
                    Expanded(
                      flex: 3,
                      child: DropdownButtonFormField<String>(
                        key: ValueKey('recipe-item-$i'),
                        initialValue: rows[i].$1,
                        isExpanded: true,
                        hint: const Text('Ingredient'),
                        items: [
                          for (final it in widget.items)
                            DropdownMenuItem(value: it['code'] as String, child: Text(it['name'])),
                        ],
                        onChanged: (v) => setState(() => rows[i] = (v, rows[i].$2)),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      flex: 2,
                      child: TextField(
                        key: ValueKey('recipe-qty-$i'),
                        controller: rows[i].$2,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: InputDecoration(suffixText: _unit(rows[i].$1), hintText: 'qty'),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Remove ingredient',
                      icon: const Icon(Icons.close),
                      onPressed: rows.length == 1 ? null : () => setState(() => rows.removeAt(i)),
                    ),
                  ],
                ),
              TextButton.icon(
                key: const ValueKey('recipe-add-row'),
                onPressed: () => setState(() => rows.add((null, TextEditingController()))),
                icon: const Icon(Icons.add),
                label: const Text('Add ingredient'),
              ),
              TextField(
                key: const ValueKey('recipe-note'),
                controller: note,
                decoration: const InputDecoration(labelText: 'Why the change (optional)'),
              ),
              const SizedBox(height: 8),
              Text(
                'Orders already fired keep the old recipe. New orders use this version.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
      actions: [
        if (widget.recipe != null)
          TextButton(
            onPressed: busy ? null : () => _publish(retire: true),
            child: Text('Retire', style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('recipe-publish'),
          onPressed: busy ? null : _publish,
          child: const Text('Publish'),
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------------------------------------ sold out

class SoldOutTab extends StatefulWidget {
  const SoldOutTab({super.key});

  @override
  State<SoldOutTab> createState() => _SoldOutTabState();
}

class _SoldOutTabState extends State<SoldOutTab> {
  Map<String, dynamic>? menu;
  Map<String, Map<String, dynamic>> soldOut = {};
  String filter = '';

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final m = await app.api.get('menu');
      final a = await app.api.get('availability');
      if (!mounted) return;
      setState(() {
        menu = m.data;
        soldOut = {
          for (final r in List<Map<String, dynamic>>.from(a.data['sold_out']))
            '${r['target_kind']}:${r['target_code']}': r,
        };
      });
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _toggle(String kind, String code, String name, bool available) async {
    String reason = '';
    if (!available) {
      final r = await askReason(
        context,
        'Mark $name sold out',
        quick: const ['Out of stock', 'Equipment down', 'Prep not ready'],
      );
      if (r == null) return;
      reason = r;
    }
    if (!mounted) return;
    await setAvailability(context, kind, code, available, reason);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final m = menu;
    if (m == null) return const Center(child: CircularProgressIndicator());
    bool match(String n) => filter.isEmpty || n.toLowerCase().contains(filter.toLowerCase());
    Widget row(String kind, String code, String name) {
      final so = soldOut['$kind:$code'];
      return SwitchListTile(
        key: ValueKey('avail-$kind-$code'),
        title: Text(name),
        subtitle: so == null
            ? null
            : Text(
                'Sold out${(so['reason'] ?? '').toString().isNotEmpty ? ' · ${so['reason']}' : ''}'
                '${so['actor'] != null ? ' · ${so['actor']}' : ''}'
                ' · ${DateFormat.Hm().format(DateTime.parse(so['since']).toLocal())}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
        value: so == null,
        onChanged: (v) => _toggle(kind, code, name, v),
      );
    }

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: TextField(
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Find menu item',
              isDense: true,
            ),
            onChanged: (v) => setState(() => filter = v),
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              children: [
                if (soldOut.isNotEmpty) SectionLabel('${soldOut.length} sold out now'),
                for (final c in List<Map<String, dynamic>>.from(m['categories'])) ...[
                  if ((c['products'] as List).any((p) => match(p['name']))) SectionLabel(c['name']),
                  for (final p in List<Map<String, dynamic>>.from(c['products']))
                    if (match(p['name'])) row('product', p['code'], p['name']),
                ],
                if ((m['option_groups'] as List).isNotEmpty) const SectionLabel('Toppings and options'),
                for (final g in List<Map<String, dynamic>>.from(m['option_groups']))
                  for (final o in List<Map<String, dynamic>>.from(g['options']))
                    if (match(o['name'])) row('option', o['code'], '${o['name']} (${g['name']})'),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// Branch sellout switch, shared by the Sold out tab and stock alerts.
Future<bool> setAvailability(BuildContext context, String kind, String code, bool available, String reason) async {
  final r = await runCorrection(context, context.read<AppState>().api, 'availability', {
    'target_kind': kind,
    'target_code': code,
    'available': available,
    'reason': reason,
  });
  return r != null;
}
