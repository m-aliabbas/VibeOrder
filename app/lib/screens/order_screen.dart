import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/menu.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';
import '../widgets/print_log.dart';
import 'pizza_builder.dart';

/// Menu + current check. "Unsent" (local draft, survives restart) is separate from "Sent to kitchen".
class OrderScreen extends StatefulWidget {
  const OrderScreen({super.key, required this.checkId, required this.title});
  final String checkId;
  final String title;

  @override
  State<OrderScreen> createState() => _OrderScreenState();
}

class _OrderScreenState extends State<OrderScreen> {
  Map<String, dynamic>? check;
  List<Map<String, dynamic>> draft = [];
  Map<String, dynamic>? pending;
  Map<String, dynamic>? draftQuote;
  String? quoteError;
  bool sending = false;
  String? category;
  int narrowTab = 0;
  Timer? _poll;
  Timer? _quoteDebounce;

  AppState get app => context.read<AppState>();
  MenuView get menu => MenuView(app.menu ?? const {});

  @override
  void initState() {
    super.initState();
    draft = app.loadDraft(widget.checkId);
    pending = app.loadPending(widget.checkId);
    _load();
    _requote();
    _poll = Timer.periodic(const Duration(seconds: 6), (_) => _load(quiet: true));
  }

  @override
  void dispose() {
    _poll?.cancel();
    _quoteDebounce?.cancel();
    super.dispose();
  }

  Future<void> _load({bool quiet = false}) async {
    try {
      final r = await app.api.get('checks/${widget.checkId}');
      if (mounted) setState(() => check = r.data);
    } on ApiError catch (e) {
      if (mounted && !quiet) showError(context, e);
    }
  }

  Future<void> _setDraft(List<Map<String, dynamic>> items) async {
    setState(() => draft = items);
    await app.saveDraft(widget.checkId, items);
    _requote();
  }

  void _requote() {
    _quoteDebounce?.cancel();
    if (draft.isEmpty) {
      setState(() {
        draftQuote = null;
        quoteError = null;
      });
      return;
    }
    _quoteDebounce = Timer(const Duration(milliseconds: 200), () async {
      try {
        final r = await app.api.post('quotes', {'preview': true, 'items': _wireItems()});
        if (mounted) {
          setState(() {
            draftQuote = r.data;
            quoteError = null;
          });
        }
      } on ApiError catch (e) {
        if (mounted) setState(() => quoteError = e.message);
      }
    });
  }

  List<Map<String, dynamic>> _wireItems() => [
    for (final it in draft)
      {
        for (final e in it.entries)
          if (!e.key.startsWith('_')) e.key: e.value,
      },
  ];

  // ---------------------------------------------------------------- adding items

  Future<void> _addProduct(Map<String, dynamic> p) async {
    if (p['available'] == false) return showError(context, '${p['name']} is sold out');
    final m = menu;
    Map<String, dynamic>? item;
    switch (p['kind']) {
      case 'pizza':
      case 'mixed_pizza':
        item = await PizzaBuilder.open(
          context,
          m,
          initial: p['kind'] == 'pizza'
              ? {'product': p['code'], 'variant': m.sizes().last['code']}
              : {'product': 'mixed-pizza'},
        );
        if (item != null) item.remove('client_line_id');
        break;
      case 'deal':
        item = await Navigator.of(context).push<Map<String, dynamic>>(
          MaterialPageRoute(
            fullscreenDialog: true,
            builder: (_) => DealBuilder(menu: m, deal: p),
          ),
        );
        break;
      default:
        final variants = List<Map<String, dynamic>>.from(p['variants']);
        String variant = variants.first['code'];
        if (variants.length > 1) {
          final v = await showModalBottomSheet<String>(
            context: context,
            builder: (_) => SafeArea(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  ListTile(
                    title: Text(p['name'], style: const TextStyle(fontWeight: FontWeight.w700)),
                  ),
                  for (final v in variants)
                    ListTile(
                      minTileHeight: 56,
                      title: Text(v['name']),
                      trailing: Text(money(v['price_minor'])),
                      onTap: () => Navigator.pop(context, v['code']),
                    ),
                ],
              ),
            ),
          );
          if (v == null) return;
          variant = v;
        }
        item = {'product': p['code'], 'variant': variant, 'quantity': 1};
    }
    if (item == null) return;
    item['fire'] = 'now';
    item['quantity'] ??= 1;
    // Same simple item tapped again: one line with a higher quantity, not a second line.
    final same = p['kind'] == 'item'
        ? draft.indexWhere(
            (d) =>
                d['product'] == item!['product'] &&
                d['variant'] == item['variant'] &&
                d['fire'] == 'now' &&
                (d['options'] == null || (d['options'] as List).isEmpty) &&
                d['notes'] == null,
          )
        : -1;
    if (same >= 0) {
      final d = draft[same];
      await _setDraft([...draft]..[same] = {...d, 'quantity': (d['quantity'] ?? 1) + (item['quantity'] as int)});
      return;
    }
    item['client_line_id'] = ApiClient.newKey();
    await _setDraft([...draft, item]);
  }

  Future<void> _editDraft(int i) async {
    final it = draft[i];
    final p = menu.productByCode(it['product']);
    if (p == null || !(p['kind'] == 'pizza' || p['kind'] == 'mixed_pizza')) return;
    final updated = await PizzaBuilder.open(context, menu, initial: it);
    if (updated == null) return;
    updated['fire'] = it['fire'];
    final next = [...draft]..[i] = updated;
    await _setDraft(next);
  }

  // ---------------------------------------------------------------- send (write protocol)

  Future<void> _send() async {
    if (sending) return;
    setState(() => sending = true);
    try {
      var p = pending;
      if (p == null) {
        final items = _wireItems();
        final q = await app.api.post('quotes', {'items': items});
        // Persist the command identity BEFORE sending so a crash/timeout retries the same key.
        p = {
          'key': ApiClient.newKey(),
          'quote_id': q.data['quote_id'],
          'menu_release': q.data['menu_release'],
          'items': items,
          'total_minor': q.data['total_minor'],
        };
        await app.savePending(widget.checkId, p);
        setState(() => pending = p);
      }
      final r = await app.api.command('checks/${widget.checkId}/rounds', {
        'quote_id': p['quote_id'],
        'menu_release': p['menu_release'],
        'items': p['items'],
      }, key: p['key']);
      await app.savePending(widget.checkId, null);
      await app.saveDraft(widget.checkId, []);
      setState(() {
        pending = null;
        draft = [];
        draftQuote = null;
      });
      if (mounted) {
        showInfo(
          context,
          r.replayed ? 'Already sent earlier — confirmed.' : 'Sent to kitchen · round ${r.data['round']}',
        );
      }
      await _load();
    } on ApiError catch (e) {
      if (e.uncertain) {
        // Keep pending: the user retries with the identical command; never guess.
        if (mounted) showError(context, '${e.message} Tap "Retry send" — it will not duplicate the order.');
      } else {
        // Definitive rejection: nothing was written. Drop the command, keep the draft.
        await app.savePending(widget.checkId, null);
        setState(() => pending = null);
        if (e.code == 'sold_out' || e.code == 'menu_changed' || e.code == 'quote_stale') {
          await app.refreshMenu();
          _requote();
        }
        if (mounted) showError(context, e);
      }
    } finally {
      if (mounted) setState(() => sending = false);
    }
  }

  Future<void> _fireHeld(List<String> lineIds) async {
    try {
      await app.api.command('checks/${widget.checkId}/fire', {'line_ids': lineIds});
      if (mounted) showInfo(context, 'Course fired');
      await _load();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _requestBill() async {
    try {
      await app.api.command('checks/${widget.checkId}/request-bill', {});
      if (mounted) showInfo(context, 'Bill requested from counter');
      await _load();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  // ---------------------------------------------------------------- corrections after send

  static const _lineReasons = ['Guest changed mind', 'Wrong entry', 'Allergy', 'Too slow'];

  Future<void> _correct(String path, Map<String, dynamic> body, String ok) async {
    final c = check;
    if (c == null) return;
    final r = await runCorrection(context, app.api, path, {'expected_revision': c['revision'], ...body});
    if (r == null) {
      await _load(quiet: true);
      return;
    }
    if (mounted) showInfo(context, (r.data['description'] ?? '').toString().isEmpty ? ok : r.data['description']);
    await _load();
  }

  Future<void> _removeLine(Map<String, dynamic> l) async {
    final reason = await askReason(context, 'Remove ${l['quantity']} × ${l['name']}?', quick: _lineReasons);
    if (reason == null) return;
    await _correct('checks/${widget.checkId}/amendments', {
      'action': 'remove',
      'line_id': l['id'],
      'reason': reason,
    }, 'Removed');
  }

  Future<void> _changeLine(Map<String, dynamic> l) async {
    final m = menu;
    final spec = Map<String, dynamic>.from(l['spec']);
    final p = m.productByCode(spec['product']);
    Map<String, dynamic>? item;
    if (p != null && (p['kind'] == 'pizza' || p['kind'] == 'mixed_pizza')) {
      item = await PizzaBuilder.open(
        context,
        m,
        initial: {...spec, 'quantity': l['quantity'], 'client_line_id': ApiClient.newKey()},
      );
    } else {
      final qty = await _askQuantity(l);
      if (qty == null) return;
      item = {...spec, 'quantity': qty, 'client_line_id': ApiClient.newKey()};
    }
    if (item == null || !mounted) return;
    item.remove('fire');
    final reason = await askReason(context, 'Why change this line?', quick: _lineReasons);
    if (reason == null) return;
    try {
      final q = await app.api.post('quotes', {
        'items': [item],
      });
      await _correct('checks/${widget.checkId}/amendments', {
        'action': 'modify',
        'line_id': l['id'],
        'item': item,
        'quote_id': q.data['quote_id'],
        'reason': reason,
      }, 'Changed');
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _splitLine(Map<String, dynamic> l) async {
    final n = (l['quantity'] as num).toInt();
    var k = 1;
    final picked = await showDialog<int>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, set) => AlertDialog(
          title: Text('Split ${l['quantity']} × ${l['name']}'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('How many go to a separate line?'),
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  IconButton.filledTonal(
                    onPressed: k > 1 ? () => set(() => k--) : null,
                    icon: const Icon(Icons.remove),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: Text('$k', key: const ValueKey('split-qty'), style: Theme.of(ctx).textTheme.headlineMedium),
                  ),
                  IconButton.filledTonal(
                    onPressed: k < n - 1 ? () => set(() => k++) : null,
                    icon: const Icon(Icons.add),
                  ),
                ],
              ),
              Text('Result: ${n - k} × and $k × (kitchen progress is kept)'),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              key: const ValueKey('split-confirm'),
              onPressed: () => Navigator.pop(ctx, k),
              child: const Text('Split'),
            ),
          ],
        ),
      ),
    );
    if (picked == null || !mounted) return;
    await _correct('checks/${widget.checkId}/amendments', {
      'action': 'split',
      'line_id': l['id'],
      'quantity': picked,
      'reason': 'split to correct one',
    }, 'Split into separate lines');
  }

  Future<int?> _askQuantity(Map<String, dynamic> l) {
    var qty = (l['quantity'] as num).toInt();
    return showDialog<int>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, set) => AlertDialog(
          title: Text('Change ${l['name']}'),
          content: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton.filledTonal(
                onPressed: qty > 1 ? () => set(() => qty--) : null,
                icon: const Icon(Icons.remove),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text('$qty', style: Theme.of(ctx).textTheme.headlineMedium),
              ),
              IconButton.filledTonal(onPressed: () => set(() => qty++), icon: const Icon(Icons.add)),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              onPressed: qty == l['quantity'] ? null : () => Navigator.pop(ctx, qty),
              child: const Text('Change'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _remakeLine(Map<String, dynamic> l) async {
    final reason = await askReason(
      context,
      'Remake ${l['name']}? The guest is not charged again.',
      quick: const ['Dropped', 'Burnt', 'Wrong item made', 'Guest complaint'],
    );
    if (reason == null) return;
    await _correct('checks/${widget.checkId}/amendments', {
      'action': 'remake',
      'line_id': l['id'],
      'reason': reason,
    }, 'Remake sent');
  }

  Future<void> _moveOrMerge({required bool merge}) async {
    try {
      final floor = (await app.api.get('floor')).data;
      if (!mounted) return;
      final tables = [
        for (final a in List<Map<String, dynamic>>.from(floor['areas']))
          for (final t in List<Map<String, dynamic>>.from(a['tables']))
            if (_pickable(t, merge)) t,
      ];
      if (tables.isEmpty) {
        return showInfo(context, merge ? 'No other occupied tables' : 'No free tables');
      }
      final picked = await showDialog<Map<String, dynamic>>(
        context: context,
        builder: (ctx) => SimpleDialog(
          title: Text(merge ? 'Merge which table into this check?' : 'Move to which table?'),
          children: [
            for (final t in tables)
              SimpleDialogOption(
                key: ValueKey('pick-table-${t['label']}'),
                onPressed: () => Navigator.pop(ctx, t),
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                child: Text(
                  merge
                      ? 'Table ${t['label']} · #${t['check']['token']} · ${money(t['check']['total_minor'])}'
                      : 'Table ${t['label']} (${t['seats']} seats)',
                ),
              ),
          ],
        ),
      );
      if (picked == null || !mounted) return;
      if (!merge) {
        await _correct('checks/${widget.checkId}/move', {
          'table_id': picked['id'],
        }, 'Moved to table ${picked['label']}');
        return;
      }
      final reason = await askReason(
        context,
        'Merge table ${picked['label']} into this check?',
        quick: const ['Party joined', 'Wrong table opened'],
      );
      if (reason == null) return;
      await _correct('checks/${widget.checkId}/merge', {
        'source_check_id': picked['check']['id'],
        'source_expected_revision': picked['check']['revision'],
        'reason': reason,
      }, 'Merged');
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  bool _pickable(Map<String, dynamic> t, bool merge) {
    final occupied = t['occupied'] == true;
    if (!merge) return !occupied;
    final other = t['check'] as Map<String, dynamic>?;
    return occupied && other != null && other['id'] != widget.checkId;
  }

  Future<void> _voidCheck() async {
    final reason = await askReason(
      context,
      'Void this whole check?',
      quick: const ['Guest left', 'Opened by mistake', 'Test order'],
    );
    if (reason == null) return;
    await _correct('checks/${widget.checkId}/void', {'reason': reason}, 'Check voided');
    if (mounted && check?['status'] == 'voided') Navigator.of(context).maybePop();
  }

  Widget? _lineMenu(Map<String, dynamic> l, bool open) {
    if (!open || l['status'] != 'active' || !app.can('check.amend')) return null;
    final statuses = List<String>.from(l['production_status'] ?? const []);
    final cooked = statuses.any((s) => s == 'ready' || s == 'packed' || s == 'served');
    final started = cooked || statuses.contains('preparing');
    return PopupMenuButton<String>(
      key: ValueKey('line-menu-${l['line_no']}'),
      tooltip: 'Correct this line',
      onSelected: (v) => switch (v) {
        'change' => _changeLine(l),
        'remove' => _removeLine(l),
        'split' => _splitLine(l),
        _ => _remakeLine(l),
      },
      itemBuilder: (_) => [
        if ((l['quantity'] as num) > 1)
          PopupMenuItem(
            value: 'split',
            child: ListTile(
              leading: const Icon(Icons.call_split),
              title: const Text('Split…'),
              subtitle: Text('Separate ${l['quantity']} × into lines to change, remove or pay one'),
            ),
          ),
        if (!cooked)
          const PopupMenuItem(
            value: 'change',
            child: ListTile(leading: Icon(Icons.edit), title: Text('Change')),
          ),
        PopupMenuItem(
          value: 'remove',
          child: ListTile(
            leading: const Icon(Icons.remove_circle_outline),
            title: const Text('Remove'),
            subtitle: cooked ? const Text('Already cooked: manager approval') : null,
          ),
        ),
        if (started)
          const PopupMenuItem(
            value: 'remake',
            child: ListTile(leading: Icon(Icons.replay), title: Text('Remake'), subtitle: Text('Manager · no charge')),
          ),
      ],
    );
  }

  // ---------------------------------------------------------------- UI

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final c = check;
    final draftTotal = (draftQuote?['total_minor'] as num?)?.toInt();
    final title = Row(
      children: [
        Text(widget.title),
        if (c != null) ...[
          const SizedBox(width: 12),
          StatusChip('#${c['token']}'),
          const SizedBox(width: 8),
          if (c['status'] != 'open') StatusChip(c['status'].toString().toUpperCase(), color: t.colorScheme.error),
        ],
      ],
    );
    final checkPanel = _checkPanel(t, draftTotal);
    final menuPanel = _menuPanel(t);
    return Scaffold(
      appBar: AppBar(
        title: title,
        actions: [
          if (c != null)
            IconButton(
              key: const ValueKey('order-prints'),
              tooltip: 'KOTs & receipts',
              onPressed: () => showOrderPrints(
                context,
                List<Map<String, dynamic>>.from(c['print_jobs'] ?? const []),
                title: 'KOTs & receipts · #${c['token']}',
              ),
              icon: const Icon(Icons.receipt_long_outlined),
            ),
          if (c != null)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: Center(
                child: Text(
                  '${(c['rounds'] as List).fold<int>(0, (a, r) => a + (r['lines'] as List).where((l) => l['status'] == 'active').length) + draft.length} items · ${money((c['total_minor'] as num) + (draftTotal ?? 0))}',
                  style: t.textTheme.titleMedium,
                ),
              ),
            ),
          if (c != null && c['status'] == 'open')
            PopupMenuButton<String>(
              key: const ValueKey('check-menu'),
              tooltip: 'Check actions',
              onSelected: (v) => switch (v) {
                'move' => _moveOrMerge(merge: false),
                'merge' => _moveOrMerge(merge: true),
                _ => _voidCheck(),
              },
              itemBuilder: (_) => [
                if (c['service_type'] == 'dine_in' && app.can('check.move')) ...[
                  const PopupMenuItem(
                    value: 'move',
                    child: ListTile(leading: Icon(Icons.swap_horiz), title: Text('Move table')),
                  ),
                  const PopupMenuItem(
                    value: 'merge',
                    child: ListTile(
                      leading: Icon(Icons.merge),
                      title: Text('Merge a table in'),
                      subtitle: Text('Manager'),
                    ),
                  ),
                ],
                const PopupMenuItem(
                  value: 'void',
                  child: ListTile(leading: Icon(Icons.block), title: Text('Void check'), subtitle: Text('Manager')),
                ),
              ],
            ),
        ],
      ),
      body: wide
          ? Row(
              children: [
                Expanded(flex: 3, child: menuPanel),
                const VerticalDivider(width: 1),
                SizedBox(width: 400, child: checkPanel),
              ],
            )
          : IndexedStack(index: narrowTab, children: [menuPanel, checkPanel]),
      bottomNavigationBar: wide
          ? null
          : NavigationBar(
              selectedIndex: narrowTab,
              onDestinationSelected: (i) => setState(() => narrowTab = i),
              destinations: [
                const NavigationDestination(icon: Icon(Icons.restaurant_menu), label: 'Menu'),
                NavigationDestination(
                  icon: Badge(
                    isLabelVisible: draft.isNotEmpty,
                    label: Text('${draft.length}'),
                    child: const Icon(Icons.receipt_long),
                  ),
                  label: 'Check',
                ),
              ],
            ),
    );
  }

  Widget _menuPanel(ThemeData t) {
    final m = menu;
    final cats = m.categories;
    if (cats.isEmpty) return const Center(child: Text('No menu published at this branch'));
    category ??= cats.firstWhere((c) => c['code'] == 'pizza', orElse: () => cats.first)['code'];
    final cat = cats.firstWhere((c) => c['code'] == category, orElse: () => cats.first);
    final products = List<Map<String, dynamic>>.from(cat['products']);
    return Column(
      children: [
        SizedBox(
          height: 64,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            children: [
              for (final c in cats)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    key: ValueKey('cat-${c['code']}'),
                    label: Text(c['name']),
                    selected: c['code'] == category,
                    onSelected: (_) => setState(() => category = c['code']),
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: GridView.extent(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            maxCrossAxisExtent: 220,
            childAspectRatio: 1.0,
            mainAxisSpacing: 10,
            crossAxisSpacing: 10,
            children: [
              for (final p in products)
                Card(
                  clipBehavior: Clip.antiAlias,
                  child: InkWell(
                    key: ValueKey('product-${p['code']}'),
                    onTap: () => _addProduct(p),
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            p['name'],
                            style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                          if ((p['name_local'] ?? '').toString().isNotEmpty)
                            Text(p['name_local'], style: t.textTheme.bodyMedium, textDirection: TextDirection.rtl),
                          const Spacer(),
                          if (p['available'] == false)
                            StatusChip('SOLD OUT', color: t.colorScheme.error, icon: Icons.block)
                          else
                            Text(
                              _fromPrice(p),
                              style: t.textTheme.bodyMedium?.copyWith(
                                color: t.colorScheme.primary,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }

  String _fromPrice(Map<String, dynamic> p) {
    if (p['kind'] == 'mixed_pizza') return 'Two flavours';
    final prices = [for (final v in p['variants']) (v['price_minor'] as num).toInt()];
    if (prices.isEmpty) return '';
    prices.sort();
    return prices.length > 1 ? 'from ${money(prices.first)}' : money(prices.first);
  }

  Widget _checkPanel(ThemeData t, int? draftTotal) {
    final c = check;
    final m = menu;
    final open = c == null || c['status'] == 'open';
    final rounds = c == null ? <Map<String, dynamic>>[] : List<Map<String, dynamic>>.from(c['rounds']);
    final held = [
      for (final r in rounds)
        for (final l in List<Map<String, dynamic>>.from(r['lines']))
          if (l['fire_state'] == 'held' && l['status'] == 'active') l,
    ];
    return Column(
      children: [
        Expanded(
          child: ListView(
            children: [
              if (pending != null)
                MaterialBanner(
                  backgroundColor: t.colorScheme.tertiaryContainer,
                  leading: const Icon(Icons.sync_problem),
                  content: Text(
                    'Last send was not confirmed (${money(pending!['total_minor'])}). Retry — it will not double the order.',
                  ),
                  actions: [
                    FilledButton(
                      key: const ValueKey('retry-send'),
                      onPressed: sending ? null : _send,
                      child: const Text('Retry send'),
                    ),
                  ],
                ),
              SectionLabel(
                'Unsent (${draft.length})',
                trailing: draft.isEmpty
                    ? null
                    : TextButton(onPressed: pending != null ? null : () => _setDraft([]), child: const Text('Clear')),
              ),
              if (draft.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text('Tap menu items to add.', style: t.textTheme.bodyMedium),
                ),
              for (var i = 0; i < draft.length; i++) _draftTile(t, m, i),
              if (held.isNotEmpty) ...[
                SectionLabel(
                  'Held courses (${held.length})',
                  trailing: FilledButton.tonalIcon(
                    key: const ValueKey('fire-held'),
                    onPressed: () => _fireHeld([for (final l in held) l['id']]),
                    icon: const Icon(Icons.local_fire_department),
                    label: const Text('Fire all'),
                  ),
                ),
                for (final l in held)
                  ListTile(
                    title: Text('${l['quantity']} × ${l['name']}'),
                    subtitle: Text(describeItem(m, Map<String, dynamic>.from(l['spec']))),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        TextButton(onPressed: () => _fireHeld([l['id']]), child: const Text('Fire')),
                        ?_lineMenu(l, open),
                      ],
                    ),
                  ),
              ],
              SectionLabel('Sent to kitchen'),
              if (rounds.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text('Nothing sent yet.', style: t.textTheme.bodyMedium),
                ),
              for (final r in rounds.reversed) ...[
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                  child: Text('Round ${r['number']} · ${r['added_by']}', style: t.textTheme.labelLarge),
                ),
                for (final l in List<Map<String, dynamic>>.from(r['lines']))
                  if (l['fire_state'] != 'held' || l['status'] != 'active') _sentTile(t, m, l, open),
              ],
              if (c != null && (c['adjustments'] as List? ?? const []).isNotEmpty) ...[
                SectionLabel('Corrections'),
                for (final a in List<Map<String, dynamic>>.from(c['adjustments']))
                  ListTile(
                    dense: true,
                    leading: Icon(switch (a['kind']) {
                      'remake' => Icons.replay,
                      'move' => Icons.swap_horiz,
                      'merge' => Icons.merge,
                      'void' => Icons.block,
                      'remove' => Icons.remove_circle_outline,
                      _ => Icons.edit,
                    }),
                    title: Text(a['description'] ?? a['kind']),
                    subtitle: Text(
                      '${a['reason']} · ${a['staff']}${a['approved_by'] != null ? ' · approved ${a['approved_by']}' : ''}',
                    ),
                    trailing: (a['amount_delta_minor'] ?? 0) == 0 ? null : Text(money(a['amount_delta_minor'])),
                  ),
              ],
            ],
          ),
        ),
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            border: Border(top: BorderSide(color: t.colorScheme.outlineVariant)),
          ),
          child: Column(
            children: [
              _row(t, 'Sent', money(c?['total_minor'])),
              if (draft.isNotEmpty)
                _row(
                  t,
                  'Unsent',
                  quoteError ?? (draftTotal == null ? '…' : money(draftTotal)),
                  color: quoteError != null ? t.colorScheme.error : null,
                ),
              if (c != null && (c['paid_minor'] ?? 0) > 0) _row(t, 'Paid', money(c['paid_minor'])),
              const SizedBox(height: 10),
              Row(
                children: [
                  if (c != null && open && c['service_type'] == 'dine_in' && app.can('check.request_bill'))
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: draft.isNotEmpty ? null : _requestBill,
                        icon: const Icon(Icons.receipt),
                        label: const Text('Bill'),
                      ),
                    ),
                  const SizedBox(width: 10),
                  Expanded(
                    flex: 2,
                    child: FilledButton.icon(
                      key: const ValueKey('send'),
                      onPressed: !open || sending || (draft.isEmpty && pending == null) || quoteError != null
                          ? null
                          : _send,
                      icon: sending
                          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.send),
                      label: Text(
                        pending != null ? 'Retry send' : 'Send ${draftTotal == null ? '' : money(draftTotal)}',
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _row(ThemeData t, String k, String v, {Color? color}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 2),
    child: Row(
      children: [
        Text(k, style: t.textTheme.bodyLarge),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            v,
            textAlign: TextAlign.end,
            style: t.textTheme.bodyLarge?.copyWith(fontWeight: FontWeight.w700, color: color),
          ),
        ),
      ],
    ),
  );

  /// Line ids that a later correction replaced (shown as "changed" rather than "removed").
  Set<String> get _replaced => {
    for (final r in List<Map<String, dynamic>>.from(check?['rounds'] ?? const []))
      for (final l in List<Map<String, dynamic>>.from(r['lines']))
        if (l['amends_id'] != null) l['amends_id'] as String,
  };

  Widget _sentTile(ThemeData t, MenuView m, Map<String, dynamic> l, bool open) {
    final voided = l['status'] != 'active';
    final style = voided ? TextStyle(decoration: TextDecoration.lineThrough, color: t.colorScheme.outline) : null;
    return ListTile(
      key: ValueKey('sent-${l['line_no']}'),
      dense: true,
      title: Text('${l['quantity']} × ${l['name']}', style: style),
      subtitle: Text(
        describeItem(m, Map<String, dynamic>.from(l['spec'])) + (l['amends_id'] != null ? '\nCorrected' : ''),
        style: style,
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(money(l['total_minor']), style: style),
              if (voided)
                StatusChip(_replaced.contains(l['id']) ? 'changed' : 'removed', color: t.colorScheme.outline)
              else
                _prodStatus(t, List<String>.from(l['production_status'])),
            ],
          ),
          ?_lineMenu(l, open),
        ],
      ),
    );
  }

  Widget _prodStatus(ThemeData t, List<String> statuses) {
    if (statuses.isEmpty) return const SizedBox();
    final s = statuses.contains('queued')
        ? 'queued'
        : statuses.contains('preparing')
        ? 'preparing'
        : statuses.first;
    final color = switch (s) {
      'ready' => Colors.green,
      'preparing' => Colors.blue,
      _ => t.colorScheme.outline,
    };
    return StatusChip(s, color: color);
  }

  Widget _draftTile(ThemeData t, MenuView m, int i) {
    final it = draft[i];
    final p = m.productByCode(it['product']);
    final priced = draftQuote == null ? null : (draftQuote!['lines'] as List).cast<Map>().elementAtOrNull(i);
    final hold = it['fire'] == 'hold';
    return ListTile(
      key: ValueKey('draft-$i'),
      onTap: pending != null ? null : () => _editDraft(i),
      title: Text('${it['quantity'] ?? 1} × ${priced?['name'] ?? p?['name'] ?? it['product']}'),
      subtitle: Text(describeItem(m, it) + (hold ? '\nHOLD — fire later' : '')),
      isThreeLine: hold,
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            key: ValueKey('draft-minus-$i'),
            tooltip: 'One less',
            visualDensity: VisualDensity.compact,
            onPressed: pending != null || (it['quantity'] ?? 1) <= 1
                ? null
                : () => _setDraft([...draft]..[i] = {...it, 'quantity': (it['quantity'] ?? 1) - 1}),
            icon: const Icon(Icons.remove_circle_outline),
          ),
          IconButton(
            key: ValueKey('draft-plus-$i'),
            tooltip: 'One more',
            visualDensity: VisualDensity.compact,
            onPressed: pending != null || (it['quantity'] ?? 1) >= 99
                ? null
                : () => _setDraft([...draft]..[i] = {...it, 'quantity': (it['quantity'] ?? 1) + 1}),
            icon: const Icon(Icons.add_circle_outline),
          ),
          if (priced != null) Text(money(priced['total_minor'])),
          IconButton(
            tooltip: hold ? 'Fire now' : 'Hold course',
            onPressed: pending != null
                ? null
                : () => _setDraft([...draft]..[i] = {...it, 'fire': hold ? 'now' : 'hold'}),
            icon: Icon(
              hold ? Icons.pause_circle : Icons.local_fire_department_outlined,
              color: hold ? t.colorScheme.tertiary : null,
            ),
          ),
          IconButton(
            tooltip: 'Remove',
            onPressed: pending != null ? null : () => _setDraft([...draft]..removeAt(i)),
            icon: const Icon(Icons.delete_outline),
          ),
        ],
      ),
    );
  }
}

/// Fixed-price deal with named slots. Preserves which pizza/drink belongs to the deal.
class DealBuilder extends StatefulWidget {
  const DealBuilder({super.key, required this.menu, required this.deal});
  final MenuView menu;
  final Map<String, dynamic> deal;

  @override
  State<DealBuilder> createState() => _DealBuilderState();
}

class _DealBuilderState extends State<DealBuilder> {
  final Map<String, List<Map<String, dynamic>>> picks = {};
  int? price;
  String? error;

  List<Map<String, dynamic>> get slots => List<Map<String, dynamic>>.from(widget.deal['slots']);

  bool get complete => slots.every((s) => (picks[s['code']] ?? []).length == s['quantity']);

  Map<String, dynamic> build_() => {
    'product': widget.deal['code'],
    'quantity': 1,
    'selections': [
      for (final s in slots)
        for (final p in picks[s['code']] ?? const []) {...p, 'slot': s['code']},
    ],
  };

  Future<void> _price() async {
    if (!complete) return;
    try {
      final r = await context.read<AppState>().api.post('quotes', {
        'preview': true,
        'items': [build_()],
      });
      setState(() {
        price = (r.data['total_minor'] as num).toInt();
        error = null;
      });
    } on ApiError catch (e) {
      setState(() => error = e.message);
    }
  }

  Future<void> _pick(Map<String, dynamic> slot) async {
    final choices = List<Map<String, dynamic>>.from(slot['choices']);
    final m = widget.menu;
    final pizzaChoices = choices
        .where((c) => ['pizza', 'mixed_pizza'].contains(m.productByCode(c['product'])?['kind']))
        .toList();
    Map<String, dynamic>? sel;
    if (pizzaChoices.isNotEmpty) {
      sel = await PizzaBuilder.open(
        context,
        m,
        fixedSize: pizzaChoices.first['variant'],
        allowedProducts: {for (final c in pizzaChoices) c['product']},
        inDeal: true,
      );
      sel?.remove('client_line_id');
    } else {
      final c = await showModalBottomSheet<Map<String, dynamic>>(
        context: context,
        builder: (_) => SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                title: Text(slot['name'], style: const TextStyle(fontWeight: FontWeight.w700)),
              ),
              for (final c in choices)
                ListTile(
                  minTileHeight: 56,
                  title: Text(m.productName(c['product'])),
                  trailing: (c['upgrade_minor'] ?? 0) > 0 ? Text('+${money(c['upgrade_minor'])}') : null,
                  onTap: () => Navigator.pop(context, c),
                ),
            ],
          ),
        ),
      );
      if (c != null) sel = {'product': c['product'], 'variant': c['variant']};
    }
    if (sel == null) return;
    setState(() {
      final list = picks[slot['code']] ?? [];
      if (list.length >= slot['quantity']) list.removeAt(0);
      picks[slot['code']] = [...list, sel!];
    });
    _price();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final m = widget.menu;
    return Scaffold(
      appBar: AppBar(title: Text(widget.deal['name'])),
      body: ListView(
        children: [
          if ((widget.deal['description'] ?? '').toString().isNotEmpty)
            Padding(padding: const EdgeInsets.all(16), child: Text(widget.deal['description'])),
          for (final s in slots) ...[
            SectionLabel('${s['name']} (${(picks[s['code']] ?? []).length}/${s['quantity']})'),
            for (final p in picks[s['code']] ?? const <Map<String, dynamic>>[])
              ListTile(
                leading: const Icon(Icons.check_circle, color: Colors.green),
                title: Text(describeItem(m, p)),
              ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: OutlinedButton.icon(
                key: ValueKey('slot-${s['code']}'),
                onPressed: () => _pick(s),
                icon: const Icon(Icons.add),
                label: Text((picks[s['code']] ?? []).isEmpty ? 'Choose ${s['name']}' : 'Change'),
              ),
            ),
          ],
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  error ?? (price == null ? 'Fill every slot' : money(price)),
                  key: const ValueKey('deal-price'),
                  style: t.textTheme.titleLarge?.copyWith(
                    fontWeight: FontWeight.w800,
                    color: error != null ? t.colorScheme.error : null,
                  ),
                ),
              ),
              FilledButton.icon(
                key: const ValueKey('deal-confirm'),
                onPressed: complete && error == null ? () => Navigator.pop(context, build_()) : null,
                icon: const Icon(Icons.check),
                label: const Text('Add deal'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
