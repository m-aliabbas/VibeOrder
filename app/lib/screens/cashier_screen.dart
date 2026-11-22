import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';
import '../api/menu.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';
import '../widgets/print_log.dart';
import 'order_screen.dart';
import 'phone_order.dart';

/// Counter: open bills on the left, the selected check with amount due, tenders and change on the right.
class CashierScreen extends StatefulWidget {
  const CashierScreen({super.key});

  @override
  State<CashierScreen> createState() => _CashierScreenState();
}

class _CashierScreenState extends State<CashierScreen> {
  List<Map<String, dynamic>> checks = [];
  Map<String, dynamic>? selected;
  Timer? timer;
  bool busy = false;
  String listStatus = 'open';
  final search = TextEditingController();

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('checks', query: {'status': listStatus});
      if (!mounted) return;
      setState(() => checks = List<Map<String, dynamic>>.from(r.data['checks']));
      if (selected != null) await _select(selected!['id'], quiet: true);
    } on ApiError catch (_) {}
  }

  Future<void> _select(String id, {bool quiet = false}) async {
    try {
      final r = await app.api.get('checks/$id');
      if (mounted) setState(() => selected = r.data);
    } on ApiError catch (e) {
      if (mounted && !quiet) showError(context, e);
    }
  }

  Future<void> _newSale(String type) async {
    var body = <String, dynamic>{'service_type': type};
    var repeatItems = <Map<String, dynamic>>[];
    if (type != 'counter') {
      final res = await showDialog<PhoneOrder>(
        context: context,
        builder: (_) => PhoneOrderDialog(api: app.api, initialType: type),
      );
      if (res == null) return;
      body = res.body;
      repeatItems = res.repeatItems;
      type = body['service_type'];
    }
    try {
      final r = await app.api.command('checks', body);
      if (!mounted) return;
      if (repeatItems.isNotEmpty) await app.saveDraft(r.data['check_id'], repeatItems);
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => OrderScreen(checkId: r.data['check_id'], title: '${_typeLabel(type)} ${r.data['token']}'),
        ),
      );
      await _load();
      await _select(r.data['check_id']);
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  String _typeLabel(String t) =>
      {'counter': 'Counter', 'pickup': 'Pickup', 'delivery': 'Delivery', 'dine_in': 'Table'}[t] ?? t;

  Future<void> _pay(String method, {List<Map<String, dynamic>>? allocations}) async {
    final c = selected!;
    final fixed = allocations?.fold<int>(0, (a, x) => a + (x['amount_minor'] as int));
    final res = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) =>
          _TenderDialog(method: method, balance: fixed ?? (c['balance_minor'] as num).toInt(), fixed: fixed != null),
    );
    if (res == null) return;
    final body = <String, dynamic>{...res, 'allocations': ?allocations};
    setState(() => busy = true);
    // One key per payment intent: a timeout is retried with the same key, never as a fresh payment.
    final key = ApiClient.newKey();
    try {
      final r = await app.api.command('checks/${c['id']}/tenders', {
        'method': method,
        'expected_revision': c['revision'],
        ...body,
      }, key: key);
      if (mounted && allocations != null) {
        final print = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text('Paid ${money(r.data['applied_minor'])} for ${allocations.length} item(s)'),
            content: Text(
              (r.data['change_minor'] ?? 0) > 0 ? 'Give change ${money(r.data['change_minor'])}.' : 'Payment recorded.',
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('No receipt')),
              FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Print payer receipt')),
            ],
          ),
        );
        if (print == true) {
          try {
            await app.api.command('tenders/${r.data['tender_id']}/receipt', {});
            if (mounted) showInfo(context, 'Payer receipt sent to printer');
          } on ApiError catch (e) {
            if (mounted) showError(context, e);
          }
        }
      } else if (mounted && method == 'cash' && (r.data['change_minor'] ?? 0) > 0) {
        await showDialog(
          context: context,
          builder: (_) => AlertDialog(
            title: const Text('Give change'),
            content: Text(
              money(r.data['change_minor']),
              key: const ValueKey('change-amount'),
              style: Theme.of(context).textTheme.displayMedium?.copyWith(fontWeight: FontWeight.w800),
            ),
            actions: [
              FilledButton(autofocus: true, onPressed: () => Navigator.pop(context), child: const Text('Done')),
            ],
          ),
        );
      }
    } on ApiError catch (e) {
      if (mounted) {
        if (e.code == 'stale_revision') {
          showError(context, 'The bill changed (new items added). Review the new total before taking payment.');
        } else if (e.uncertain) {
          showError(context, 'Payment not confirmed. Check the bill before retrying — do not take money twice.');
        } else {
          showError(context, e);
        }
      }
    } finally {
      if (mounted) setState(() => busy = false);
      await _select(c['id']);
    }
  }

  Future<void> _close() async {
    final c = selected!;
    setState(() => busy = true);
    try {
      final r = await app.api.command('checks/${c['id']}/close', {'expected_revision': c['revision']});
      if (mounted) showInfo(context, 'Closed · receipt ${r.data['receipt_no']} sent to printer');
      setState(() => selected = null);
      await _load();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
      await _select(c['id']);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  // ---------------------------------------------------------------- printing

  Future<void> _invoice(Map<String, dynamic> c) async {
    try {
      final r = await app.api.get('checks/${c['id']}/invoice');
      final base = app.server.endsWith('/') ? app.server.substring(0, app.server.length - 1) : app.server;
      final ok = await launchUrl(Uri.parse('$base${r.data['path']}'), webOnlyWindowName: '_blank');
      if (!ok && mounted) showError(context, 'Could not open the invoice');
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _reprintReceipt(Map<String, dynamic> c) async {
    final jobs = List<Map<String, dynamic>>.from(c['print_jobs'] ?? const []);
    final original = jobs.where((j) => j['kind'] == 'receipt' && j['is_copy'] != true).firstOrNull;
    if (original == null) return showError(context, 'No receipt was printed for this bill (no receipt printer?)');
    if (await reprintCopy(context, original)) await _select(c['id'], quiet: true);
  }

  Future<void> _printBill(Map<String, dynamic> c) async {
    try {
      await app.api.command('checks/${c['id']}/request-bill', {});
      if (mounted) showInfo(context, 'Bill (not a receipt) sent to the counter printer');
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
    await _select(c['id'], quiet: true);
  }

  // ---------------------------------------------------------------- split by item

  Future<void> _splitByItem() async {
    final c = selected!;
    final paid = <String, int>{};
    for (final tn in c['tenders']) {
      if (tn['status'] != 'succeeded') continue;
      for (final a in tn['lines'] ?? const []) {
        paid[a['line_id']] = (paid[a['line_id']] ?? 0) + (a['amount_minor'] as num).toInt();
      }
    }
    final lines = [
      for (final r in c['rounds'])
        for (final l in r['lines'])
          if (l['status'] == 'active' && (l['charge_minor'] as num) - (paid[l['id']] ?? 0) > 0)
            {
              ...Map<String, dynamic>.from(l),
              'unpaid_minor': (l['charge_minor'] as num).toInt() - (paid[l['id']] ?? 0),
            },
    ];
    final picked = await showDialog<(String, List<Map<String, dynamic>>)>(
      context: context,
      builder: (_) => _SplitDialog(lines: lines),
    );
    if (picked == null || !mounted) return;
    await _pay(picked.$1, allocations: picked.$2);
  }

  // ---------------------------------------------------------------- discounts

  Future<void> _discount() async {
    final c = selected!;
    Map<String, dynamic> info;
    try {
      info = (await app.api.get('checks/${c['id']}/discounts')).data;
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
      return;
    }
    if (!mounted) return;
    final lines = [
      for (final r in c['rounds'])
        for (final l in r['lines'])
          if (l['status'] == 'active' && l['product'] != 'delivery-fee') Map<String, dynamic>.from(l),
    ];
    final body = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _DiscountDialog(
        promotions: List<Map<String, dynamic>>.from(info['promotions']),
        limitBp: (info['limit_bp'] as num).toInt(),
        lines: lines,
        hasPromotion: (c['discounts'] as List? ?? const []).any(
          (d) => d['status'] == 'active' && d['kind'] == 'promotion',
        ),
      ),
    );
    if (body == null || !mounted) return;
    final r = await runCorrection(context, app.api, 'checks/${c['id']}/discounts', {
      'expected_revision': c['revision'],
      ...body,
    });
    if (r != null && mounted) showInfo(context, 'Discount applied · new total ${money(r.data['total_minor'])}');
    await _select(c['id'], quiet: true);
  }

  Future<void> _removeDiscount(Map<String, dynamic> d) async {
    final c = selected!;
    final r = await runCorrection(context, app.api, 'checks/${c['id']}/discounts/${d['id']}/remove', {
      'expected_revision': c['revision'],
      'reason': 'removed at counter',
    });
    if (r != null && mounted) showInfo(context, 'Discount removed');
    await _select(c['id'], quiet: true);
  }

  // ---------------------------------------------------------------- refunds

  Future<void> _refund(Map<String, dynamic> tender) async {
    final c = selected!;
    final res = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _RefundDialog(
        tender: tender,
        lines: {
          for (final r in c['rounds'])
            for (final l in r['lines']) l['id'] as String: Map<String, dynamic>.from(l),
        },
      ),
    );
    if (res == null || !mounted) return;
    setState(() => busy = true);
    final r = await runCorrection(context, app.api, 'tenders/${tender['id']}/refunds', {
      'expected_revision': c['revision'],
      ...res,
    });
    if (mounted) setState(() => busy = false);
    if (r != null && mounted) {
      showInfo(
        context,
        r.data['status'] == 'pending'
            ? 'Refund recorded as PENDING — confirm it with the terminal result'
            : 'Refunded ${money(r.data['amount_minor'])}${tender['method'] == 'cash' ? ' — pay out from the drawer' : ''}',
      );
    }
    await _select(c['id'], quiet: true);
  }

  Future<void> _resolve(Map<String, dynamic> refund) async {
    final ref = TextEditingController();
    final outcome = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Card refund ${money(refund['amount_minor'])}'),
        content: SizedBox(
          width: 380,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text('What did the card terminal say?'),
              const SizedBox(height: 12),
              TextField(
                controller: ref,
                decoration: const InputDecoration(labelText: 'Terminal refund reference'),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, 'failed'), child: const Text('It failed')),
          FilledButton(onPressed: () => Navigator.pop(ctx, 'succeeded'), child: const Text('It succeeded')),
        ],
      ),
    );
    if (outcome == null || !mounted) return;
    final r = await runCorrection(context, app.api, 'refunds/${refund['id']}/resolve', {
      'status': outcome,
      'external_ref': ref.text.trim(),
    });
    if (r != null && mounted) showInfo(context, 'Refund marked $outcome');
    await _select(selected!['id'], quiet: true);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final q = search.text.toLowerCase();
    final list = checks
        .where(
          (c) =>
              q.isEmpty ||
              c['token'].toString().toLowerCase().contains(q) ||
              (c['table']?['label'] ?? '').toString().toLowerCase().contains(q) ||
              c['customer_phone'].toString().contains(q),
        )
        .toList();
    final left = Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.icon(
                key: const ValueKey('new-counter'),
                onPressed: () => _newSale('counter'),
                icon: const Icon(Icons.add_shopping_cart),
                label: const Text('Counter sale'),
              ),
              OutlinedButton.icon(
                key: const ValueKey('new-phone'),
                onPressed: () => _newSale('pickup'),
                icon: const Icon(Icons.phone),
                label: const Text('Phone pickup'),
              ),
              OutlinedButton.icon(
                key: const ValueKey('new-delivery'),
                onPressed: () => _newSale('delivery'),
                icon: const Icon(Icons.delivery_dining),
                label: const Text('Delivery'),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: SegmentedButton<String>(
            key: const ValueKey('bill-status'),
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: 'open', label: Text('Open')),
              ButtonSegment(value: 'closed', label: Text('Closed')),
            ],
            selected: {listStatus},
            onSelectionChanged: (v) {
              setState(() {
                listStatus = v.first;
                checks = [];
                selected = null;
              });
              _load();
            },
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(12),
          child: TextField(
            controller: search,
            decoration: const InputDecoration(
              isDense: true,
              hintText: 'Search token, table or phone',
              prefixIcon: Icon(Icons.search),
            ),
            onChanged: (_) => setState(() {}),
          ),
        ),
        Expanded(
          child: list.isEmpty
              ? Center(child: Text(listStatus == 'open' ? 'No open bills' : 'No closed bills'))
              : ListView(
                  children: [
                    for (final c in list)
                      ListTile(
                        key: ValueKey('open-${c['token']}'),
                        minTileHeight: 64,
                        selected: selected?['id'] == c['id'],
                        leading: CircleAvatar(
                          child: Icon(c['service_type'] == 'dine_in' ? Icons.table_restaurant : Icons.shopping_bag),
                        ),
                        title: Text(
                          c['table'] != null
                              ? 'Table ${c['table']['label']} · #${c['token']}'
                              : '${_typeLabel(c['service_type'])} #${c['token']}',
                        ),
                        subtitle: Text(
                          '${c['receipt_no'].toString().isNotEmpty ? '${c['receipt_no']} · ' : ''}${c['opened_by']}${(c['customer_name'] ?? '').toString().isNotEmpty ? ' · ${c['customer_name']}' : ''}',
                        ),
                        trailing: Text(
                          money(listStatus == 'open' ? c['balance_minor'] : c['total_minor']),
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                        onTap: () => _select(c['id']),
                      ),
                  ],
                ),
        ),
      ],
    );
    final right = selected == null ? const Center(child: Text('Select a bill')) : _detail(t, selected!);
    if (wide) {
      return Row(
        children: [
          SizedBox(width: 360, child: left),
          const VerticalDivider(width: 1),
          Expanded(child: right),
        ],
      );
    }
    return selected == null
        ? left
        : PopScope(
            canPop: false,
            onPopInvokedWithResult: (_, _) => setState(() => selected = null),
            child: Column(
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: () => setState(() => selected = null),
                    icon: const Icon(Icons.arrow_back),
                    label: const Text('Bills'),
                  ),
                ),
                Expanded(child: right),
              ],
            ),
          );
  }

  Widget _detail(ThemeData t, Map<String, dynamic> c) {
    final m = MenuView(app.menu ?? const {});
    final balance = (c['balance_minor'] as num).toInt();
    final lines = [
      for (final r in c['rounds']) ...List<Map<String, dynamic>>.from(r['lines']).where((l) => l['status'] == 'active'),
    ];
    final open = c['status'] == 'open';
    final title = c['table'] != null ? 'Table ${c['table']['label']}' : _typeLabel(c['service_type']);
    return CallbackShortcuts(
      bindings: {
        const SingleActivator(LogicalKeyboardKey.f2): () => balance > 0 ? _pay('cash') : null,
        const SingleActivator(LogicalKeyboardKey.f3): () => balance > 0 ? _pay('external_card') : null,
        const SingleActivator(LogicalKeyboardKey.f4): () => balance == 0 && open ? _close() : null,
      },
      child: Focus(
        autofocus: true,
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              color: t.colorScheme.surfaceContainerLow,
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '$title · #${c['token']}',
                          key: const ValueKey('check-title'),
                          style: t.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
                        ),
                        Text(
                          '${open ? '' : '${c['status'].toString().toUpperCase()} ${c['receipt_no'] ?? ''} · '}'
                          'Rev ${c['revision']} · opened by ${c['opened_by']} · ${c['guest_count']} guests',
                          style: t.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    key: const ValueKey('check-prints'),
                    tooltip: 'KOTs & receipts',
                    onPressed: () => showOrderPrints(
                      context,
                      List<Map<String, dynamic>>.from(c['print_jobs'] ?? const []),
                      title: 'KOTs & receipts · #${c['token']}',
                    ),
                    icon: const Icon(Icons.receipt_long_outlined),
                  ),
                  TextButton.icon(
                    key: const ValueKey('invoice'),
                    onPressed: () => _invoice(c),
                    icon: const Icon(Icons.description_outlined),
                    label: Text(c['status'] == 'closed' ? 'Invoice' : 'Pro-forma'),
                  ),
                  if (c['status'] == 'closed')
                    TextButton.icon(
                      key: const ValueKey('reprint-receipt'),
                      onPressed: () => _reprintReceipt(c),
                      icon: const Icon(Icons.print),
                      label: const Text('Reprint receipt'),
                    )
                  else if (open && lines.isNotEmpty)
                    TextButton.icon(
                      key: const ValueKey('print-bill'),
                      onPressed: busy ? null : () => _printBill(c),
                      icon: const Icon(Icons.print_outlined),
                      label: const Text('Print bill'),
                    ),
                  const SizedBox(width: 8),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text('Amount due', style: t.textTheme.labelLarge),
                      Text(
                        money(balance),
                        key: const ValueKey('amount-due'),
                        style: t.textTheme.headlineMedium?.copyWith(
                          fontWeight: FontWeight.w900,
                          color: t.colorScheme.primary,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                children: [
                  for (final l in lines)
                    ListTile(
                      dense: true,
                      title: Text('${l['quantity']} × ${l['name']}${l['fire_state'] == 'held' ? '  (held)' : ''}'),
                      subtitle: Text(describeItem(m, Map<String, dynamic>.from(l['spec']))),
                      trailing: Text(money(l['total_minor'])),
                    ),
                  const Divider(),
                  _kv(t, 'Subtotal', money(c['subtotal_minor'])),
                  for (final d in List<Map<String, dynamic>>.from(c['discounts'] ?? const []))
                    if (d['status'] == 'active')
                      _kv(
                        t,
                        d['kind'] == 'promotion'
                            ? 'Promotion ${d['promotion']?['name'] ?? ''}'
                            : 'Adjustment · ${d['reason']}${d['approved_by'] != null ? ' (${d['approved_by']})' : ''}',
                        money(-(d['applied_minor'] as num)),
                        action: open && app.can('discount.apply')
                            ? IconButton(
                                tooltip: 'Remove discount',
                                onPressed: busy ? null : () => _removeDiscount(d),
                                icon: const Icon(Icons.close, size: 18),
                              )
                            : null,
                      ),
                  if ((c['discount_minor'] ?? 0) > 0) _kv(t, 'Discount total', money(-c['discount_minor'])),
                  if (c['delivery'] != null)
                    _kv(
                      t,
                      'Delivery ${c['delivery']['zone']} · ${c['delivery']['payment'] == 'cod' ? 'cash on delivery' : 'prepaid'} · '
                          '${c['delivery']['status'].toString().replaceAll('_', ' ')}',
                      '',
                    ),
                  if ((c['tax_minor'] ?? 0) > 0) _kv(t, 'Tax', money(c['tax_minor'])),
                  _kv(t, 'Total', money(c['total_minor']), bold: true),
                  for (final tn in c['tenders'])
                    _kv(
                      t,
                      '${const {'cash': 'Cash', 'rider_cod': 'Rider COD', 'online': 'Online'}[tn['method']] ?? 'Card ${tn['external_ref']}'}'
                      '${tn['change_minor'] > 0 ? ' (change ${money(tn['change_minor'])})' : ''}',
                      money(-tn['amount_minor']),
                      action: (tn['refundable_minor'] ?? 0) > 0 && app.can('tender.take')
                          ? TextButton.icon(
                              key: ValueKey('refund-${tn['id']}'),
                              onPressed: busy ? null : () => _refund(Map<String, dynamic>.from(tn)),
                              icon: const Icon(Icons.undo, size: 18),
                              label: const Text('Refund'),
                            )
                          : null,
                    ),
                  for (final rf in List<Map<String, dynamic>>.from(c['refunds'] ?? const []))
                    _kv(
                      t,
                      'Refund ${rf['method'] == 'cash' ? 'cash' : 'card ${rf['external_ref']}'} · ${rf['reason']}'
                      '${rf['status'] == 'succeeded' ? '' : ' (${rf['status'].toString().toUpperCase()})'}',
                      money(rf['amount_minor']),
                      action: rf['status'] == 'pending'
                          ? TextButton(onPressed: () => _resolve(rf), child: const Text('Resolve'))
                          : null,
                    ),
                  _kv(t, 'Balance', money(balance), bold: true),
                ],
              ),
            ),
            if (open && balance > 0)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: Row(
                  children: [
                    if (app.can('discount.apply'))
                      Expanded(
                        child: OutlinedButton.icon(
                          key: const ValueKey('discount'),
                          onPressed: busy ? null : _discount,
                          icon: const Icon(Icons.percent),
                          label: const Text('Discount'),
                        ),
                      ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const ValueKey('split-items'),
                        onPressed: busy ? null : _splitByItem,
                        icon: const Icon(Icons.call_split),
                        label: const Text('Split by item'),
                      ),
                    ),
                  ],
                ),
              ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  Expanded(
                    child: FilledButton.icon(
                      key: const ValueKey('pay-cash'),
                      onPressed: busy || balance <= 0 ? null : () => _pay('cash'),
                      icon: const Icon(Icons.payments),
                      label: const Text('Cash  F2'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: FilledButton.tonalIcon(
                      key: const ValueKey('pay-card'),
                      onPressed: busy || balance <= 0 ? null : () => _pay('external_card'),
                      icon: const Icon(Icons.credit_card),
                      label: const Text('Card  F3'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: FilledButton.icon(
                      key: const ValueKey('close-check'),
                      style: FilledButton.styleFrom(backgroundColor: Colors.green.shade700),
                      onPressed: busy || !open || balance != 0 || lines.isEmpty ? null : _close,
                      icon: const Icon(Icons.receipt_long),
                      label: const Text('Close & receipt  F4'),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _kv(ThemeData t, String k, String v, {bool bold = false, Widget? action}) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 3),
    child: Row(
      children: [
        Expanded(child: Text(k, style: bold ? t.textTheme.titleMedium : t.textTheme.bodyMedium)),
        ?action,
        Text(
          v,
          style: (bold ? t.textTheme.titleMedium : t.textTheme.bodyMedium)?.copyWith(
            fontWeight: bold ? FontWeight.w800 : null,
          ),
        ),
      ],
    ),
  );
}

class _TenderDialog extends StatefulWidget {
  const _TenderDialog({required this.method, required this.balance, this.fixed = false});
  final String method;
  final int balance;
  final bool fixed;

  @override
  State<_TenderDialog> createState() => _TenderDialogState();
}

class _TenderDialogState extends State<_TenderDialog> {
  late final amount = TextEditingController(
    text: (widget.balance / 100).toStringAsFixed(widget.balance % 100 == 0 ? 0 : 2),
  );
  final received = TextEditingController();
  final ref = TextEditingController();
  String? error;

  int? get amt => parseRupees(amount.text);
  int? get rec => received.text.trim().isEmpty ? amt : parseRupees(received.text);

  void _submit() {
    final a = amt;
    if (a == null || a <= 0) return setState(() => error = 'Enter an amount');
    if (a > widget.balance) return setState(() => error = 'More than the balance due');
    if (widget.method == 'cash') {
      final r = rec;
      if (r == null || r < a) return setState(() => error = 'Cash received is less than the amount');
      Navigator.pop(context, {'amount_minor': a, 'cash_received_minor': r});
    } else {
      if (ref.text.trim().isEmpty) return setState(() => error = 'Enter the terminal reference');
      Navigator.pop(context, {'amount_minor': a, 'external_ref': ref.text.trim()});
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final cash = widget.method == 'cash';
    final change = cash && amt != null && rec != null && rec! >= amt! ? rec! - amt! : null;
    return AlertDialog(
      title: Text(cash ? 'Cash payment' : 'Card on external terminal'),
      content: SizedBox(
        width: 380,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Balance due ${money(widget.balance)}', style: t.textTheme.titleMedium),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('tender-amount'),
              controller: amount,
              readOnly: widget.fixed,
              decoration: InputDecoration(
                labelText: 'Amount to pay (Rs)',
                helperText: widget.fixed ? 'Selected items' : 'Lower it to split the bill',
              ),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => _submit(),
            ),
            const SizedBox(height: 12),
            if (cash) ...[
              TextField(
                key: const ValueKey('tender-received'),
                controller: received,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'Cash received (Rs)'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                onChanged: (_) => setState(() {}),
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  for (final note in [500, 1000, 2000, 5000])
                    ActionChip(label: Text('Rs $note'), onPressed: () => setState(() => received.text = '$note')),
                ],
              ),
              const SizedBox(height: 12),
              Text(
                'Change: ${change == null ? '—' : money(change)}',
                style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
              ),
            ] else
              TextField(
                key: const ValueKey('tender-ref'),
                controller: ref,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'Terminal approval / reference'),
                onSubmitted: (_) => _submit(),
              ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(error!, style: TextStyle(color: t.colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(key: const ValueKey('tender-confirm'), onPressed: _submit, child: const Text('Take payment')),
      ],
    );
  }
}

/// Refund amount, reason, what happened to the goods and (card) the terminal refund reference.
class _RefundDialog extends StatefulWidget {
  const _RefundDialog({required this.tender, this.lines = const {}});
  final Map<String, dynamic> tender;
  final Map<String, Map<String, dynamic>> lines;

  @override
  State<_RefundDialog> createState() => _RefundDialogState();
}

class _RefundDialogState extends State<_RefundDialog> {
  late final amount = TextEditingController(text: ((widget.tender['refundable_minor'] as num) ~/ 100).toString());
  final reason = TextEditingController();
  final ref = TextEditingController();
  String goods = 'not_returned';
  String? error;

  final qty = <String, int>{};

  bool get card => widget.tender['method'] != 'cash';

  List<Map<String, dynamic>> get allocs => [
    for (final a in List<Map<String, dynamic>>.from(widget.tender['lines'] ?? const []))
      if (widget.lines[a['line_id']] != null) a,
  ];

  /// Refund for q of the line's units, from what this payment paid for that line (last unit takes the remainder).
  int _share(Map<String, dynamic> a, int q) {
    final n = (widget.lines[a['line_id']]!['quantity'] as num).toInt();
    final paid = (a['amount_minor'] as num).toInt();
    return q >= n ? paid : paid * q ~/ n;
  }

  List<Map<String, dynamic>> get items => [
    for (final a in allocs)
      if ((qty[a['line_id']] ?? 0) > 0) {'line_id': a['line_id'], 'amount_minor': _share(a, qty[a['line_id']]!)},
  ];

  void _setQty(String id, int q) {
    setState(() {
      qty[id] = q;
      final total = items.fold<int>(0, (s, i) => s + (i['amount_minor'] as int));
      amount.text = (total / 100).toStringAsFixed(total % 100 == 0 ? 0 : 2);
    });
  }

  void _ok() {
    final v = parseRupees(amount.text);
    final max = (widget.tender['refundable_minor'] as num).toInt();
    if (v == null || v <= 0 || v > max) return setState(() => error = 'Enter up to ${money(max)}');
    if (reason.text.trim().isEmpty) return setState(() => error = 'Give a reason');
    Navigator.pop(context, {
      'amount_minor': v,
      'reason': reason.text.trim(),
      'goods': goods,
      if (items.isNotEmpty && items.fold<int>(0, (s, i) => s + (i['amount_minor'] as int)) == v) 'items': items,
      if (card) 'external_ref': ref.text.trim(),
    });
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Refund ${card ? 'card' : 'cash'} payment'),
      content: SizedBox(
        width: 440,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Refundable: ${money(widget.tender['refundable_minor'])} of ${money(widget.tender['amount_minor'])}'),
            if (allocs.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Text('Refund items (or type an amount below)'),
              for (final a in allocs)
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${widget.lines[a['line_id']]!['quantity']} × ${widget.lines[a['line_id']]!['name']}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    IconButton(
                      onPressed: (qty[a['line_id']] ?? 0) > 0
                          ? () => _setQty(a['line_id'], (qty[a['line_id']] ?? 0) - 1)
                          : null,
                      icon: const Icon(Icons.remove_circle_outline),
                    ),
                    Text(
                      '${qty[a['line_id']] ?? 0}',
                      key: ValueKey('refund-qty-${widget.lines[a['line_id']]!['line_no']}'),
                    ),
                    IconButton(
                      key: ValueKey('refund-plus-${widget.lines[a['line_id']]!['line_no']}'),
                      onPressed: (qty[a['line_id']] ?? 0) < (widget.lines[a['line_id']]!['quantity'] as num)
                          ? () => _setQty(a['line_id'], (qty[a['line_id']] ?? 0) + 1)
                          : null,
                      icon: const Icon(Icons.add_circle_outline),
                    ),
                  ],
                ),
            ],
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('refund-amount'),
              controller: amount,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Amount (Rs)'),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final q in const ['Cold food', 'Wrong order', 'Missing item', 'Overcharged'])
                  ChoiceChip(
                    label: Text(q),
                    selected: reason.text == q,
                    onSelected: (_) => setState(() => reason.text = q),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            TextField(
              key: const ValueKey('refund-reason'),
              controller: reason,
              decoration: const InputDecoration(labelText: 'Reason'),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: goods,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Goods'),
              items: const [
                DropdownMenuItem(value: 'not_returned', child: Text('Kept by guest / eaten')),
                DropdownMenuItem(value: 'discarded', child: Text('Returned and discarded')),
                DropdownMenuItem(value: 'returned', child: Text('Returned to stock (sealed)')),
                DropdownMenuItem(value: 'not_applicable', child: Text('Not applicable (overcharge)')),
              ],
              onChanged: (v) => setState(() => goods = v ?? goods),
            ),
            if (card) ...[
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('refund-ref'),
                controller: ref,
                decoration: const InputDecoration(
                  labelText: 'Terminal refund reference',
                  helperText: 'Leave empty if the terminal result is unknown — refund stays PENDING',
                ),
              ),
            ],
            if (error != null) ...[
              const SizedBox(height: 8),
              Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(key: const ValueKey('refund-ok'), onPressed: _ok, child: const Text('Refund')),
      ],
    );
  }
}

/// Pick the items this payer pays for; returns the method and per-line allocations.
class _SplitDialog extends StatefulWidget {
  const _SplitDialog({required this.lines});
  final List<Map<String, dynamic>> lines;

  @override
  State<_SplitDialog> createState() => _SplitDialogState();
}

class _SplitDialogState extends State<_SplitDialog> {
  final picked = <String>{};

  int get total =>
      widget.lines.where((l) => picked.contains(l['id'])).fold(0, (a, l) => a + (l['unpaid_minor'] as int));

  List<Map<String, dynamic>> get allocations => [
    for (final l in widget.lines)
      if (picked.contains(l['id'])) {'line_id': l['id'], 'amount_minor': l['unpaid_minor']},
  ];

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return AlertDialog(
      title: const Text('Split by item'),
      content: SizedBox(
        width: 440,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Tick what this guest pays for.'),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final l in widget.lines)
                    CheckboxListTile(
                      key: ValueKey('split-${l['line_no']}'),
                      value: picked.contains(l['id']),
                      title: Text('${l['quantity']} × ${l['name']}'),
                      secondary: Text(money(l['unpaid_minor'])),
                      onChanged: (v) => setState(() => v == true ? picked.add(l['id']) : picked.remove(l['id'])),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            Text('This payer: ${money(total)}', style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        OutlinedButton(
          onPressed: picked.isEmpty ? null : () => Navigator.pop(context, ('external_card', allocations)),
          child: const Text('Card'),
        ),
        FilledButton(
          key: const ValueKey('split-cash'),
          onPressed: picked.isEmpty ? null : () => Navigator.pop(context, ('cash', allocations)),
          child: const Text('Cash'),
        ),
      ],
    );
  }
}

/// One order promotion, or a line adjustment (above the branch limit a manager approves).
class _DiscountDialog extends StatefulWidget {
  const _DiscountDialog({
    required this.promotions,
    required this.limitBp,
    required this.lines,
    required this.hasPromotion,
  });
  final List<Map<String, dynamic>> promotions;
  final int limitBp;
  final List<Map<String, dynamic>> lines;
  final bool hasPromotion;

  @override
  State<_DiscountDialog> createState() => _DiscountDialogState();
}

class _DiscountDialogState extends State<_DiscountDialog> {
  late String mode = widget.hasPromotion ? 'line' : 'promotion';
  String? promo;
  String? lineId;
  bool percent = true;
  final value = TextEditingController();
  final reason = TextEditingController();
  String? error;

  void _ok() {
    if (mode == 'promotion') {
      if (promo == null) return setState(() => error = 'Choose a promotion');
      return Navigator.pop(context, {'kind': 'promotion', 'promotion_code': promo, 'reason': reason.text.trim()});
    }
    if (lineId == null) return setState(() => error = 'Choose the item');
    if (reason.text.trim().isEmpty) return setState(() => error = 'Give a reason');
    final body = <String, dynamic>{'kind': 'line_adjustment', 'line_id': lineId, 'reason': reason.text.trim()};
    if (percent) {
      final p = double.tryParse(value.text.trim());
      if (p == null || p <= 0 || p > 100) return setState(() => error = 'Percent must be 1-100');
      body['percent_bp'] = (p * 100).round();
    } else {
      final a = parseRupees(value.text);
      if (a == null || a <= 0) return setState(() => error = 'Enter an amount');
      body['amount_minor'] = a;
    }
    Navigator.pop(context, body);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return AlertDialog(
      title: const Text('Discount'),
      content: SizedBox(
        width: 460,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SegmentedButton<String>(
                showSelectedIcon: false,
                segments: [
                  ButtonSegment(value: 'promotion', label: const Text('Promotion'), enabled: !widget.hasPromotion),
                  const ButtonSegment(value: 'line', label: Text('Item adjustment')),
                ],
                selected: {mode},
                onSelectionChanged: (v) => setState(() => mode = v.first),
              ),
              const SizedBox(height: 12),
              if (mode == 'promotion')
                for (final p in widget.promotions)
                  ListTile(
                    key: ValueKey('promo-${p['code']}'),
                    selected: promo == p['code'],
                    leading: Icon(promo == p['code'] ? Icons.radio_button_checked : Icons.radio_button_off),
                    title: Text(p['name']),
                    subtitle: Text(
                      '${p['code']}${(p['min_subtotal_minor'] ?? 0) > 0 ? ' · min ${money(p['min_subtotal_minor'])}' : ''}'
                      '${p['requires_approval'] == true ? ' · manager approval' : ''}',
                    ),
                    onTap: () => setState(() => promo = p['code']),
                  )
              else ...[
                DropdownButtonFormField<String>(
                  initialValue: lineId,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Item'),
                  items: [
                    for (final l in widget.lines)
                      DropdownMenuItem(
                        value: l['id'] as String,
                        child: Text('${l['quantity']} × ${l['name']} · ${money(l['total_minor'])}'),
                      ),
                  ],
                  onChanged: (v) => setState(() => lineId = v),
                ),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        key: const ValueKey('discount-value'),
                        controller: value,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: InputDecoration(labelText: percent ? 'Percent' : 'Amount (Rs)'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    SegmentedButton<bool>(
                      showSelectedIcon: false,
                      segments: const [
                        ButtonSegment(value: true, label: Text('%')),
                        ButtonSegment(value: false, label: Text('Rs')),
                      ],
                      selected: {percent},
                      onSelectionChanged: (v) => setState(() => percent = v.first),
                    ),
                  ],
                ),
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    'Up to ${widget.limitBp / 100}% per item without a manager.',
                    style: t.textTheme.bodySmall,
                  ),
                ),
              ],
              TextField(
                key: const ValueKey('discount-reason'),
                controller: reason,
                decoration: InputDecoration(labelText: mode == 'promotion' ? 'Note (optional)' : 'Reason'),
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(error!, style: TextStyle(color: t.colorScheme.error)),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(key: const ValueKey('discount-apply'), onPressed: _ok, child: const Text('Apply')),
      ],
    );
  }
}
