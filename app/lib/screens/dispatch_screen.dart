import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';

String _time(dynamic iso) => iso == null ? '—' : DateFormat.Hm().format(DateTime.parse(iso).toLocal());

/// Packing checklist (all components across stations) + delivery dispatch. Usable without a rider phone app.
class DispatchScreen extends StatefulWidget {
  const DispatchScreen({super.key});

  @override
  State<DispatchScreen> createState() => _DispatchScreenState();
}

class _DispatchScreenState extends State<DispatchScreen> {
  List<Map<String, dynamic>> orders = [];
  List<Map<String, dynamic>> deliveries = [];
  List<Map<String, dynamic>> riders = [];
  Timer? timer;
  bool busy = false;

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
      final p = await app.api.get('packing');
      final d = await app.api.get('deliveries');
      final r = await app.api.get('riders');
      if (!mounted) return;
      setState(() {
        orders = List<Map<String, dynamic>>.from(p.data['orders']);
        deliveries = List<Map<String, dynamic>>.from(d.data['deliveries']);
        riders = List<Map<String, dynamic>>.from(r.data['riders']);
      });
    } on ApiError catch (_) {}
  }

  Future<void> _run(String path, Map<String, dynamic> body, String done) async {
    setState(() => busy = true);
    final r = await runCorrection(context, app.api, path, body);
    if (mounted) {
      setState(() => busy = false);
      if (r != null) showInfo(context, done);
    }
    await _load();
  }

  Future<void> _markReady(Map<String, dynamic> comp) async {
    try {
      await app.api.command('ticket-items/${comp['id']}/transitions', {
        'to': 'ready',
        'expected_revision': comp['revision'],
      });
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
    await _load();
  }

  Future<void> _handover(Map<String, dynamic> o) async {
    final body = <String, dynamic>{'expected_revision': o['revision']};
    if (o['complete'] != true) {
      final reason = await askReason(
        context,
        'Hand over with missing items?',
        quick: const ['Guest will collect later', 'Item unavailable, guest agreed', 'Refund agreed'],
      );
      if (reason == null) return;
      body['override_reason'] = reason;
    }
    if (!mounted) return;
    await _run(
      'checks/${o['id']}/handover',
      body,
      o['service_type'] == 'delivery' ? '#${o['token']} packed for the rider' : '#${o['token']} handed over',
    );
  }

  Future<void> _assign(Map<String, dynamic> d) async {
    final rider = await showDialog<String>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: Text('Rider for #${d['token']}'),
        children: [
          for (final r in riders)
            SimpleDialogOption(
              key: ValueKey('rider-${r['name']}'),
              onPressed: () => Navigator.pop(ctx, r['id'] as String),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text('${r['name']} · ${r['active_deliveries']} active · owes ${money(r['outstanding_minor'])}'),
              ),
            ),
        ],
      ),
    );
    if (rider == null || !mounted) return;
    await _move(d, 'assigned', {'rider_id': rider}, 'Rider assigned');
  }

  Future<void> _move(Map<String, dynamic> d, String to, Map<String, dynamic> extra, String done) =>
      _run('deliveries/${d['id']}/transitions', {'to': to, 'expected_revision': d['revision'], ...extra}, done);

  Future<void> _delivered(Map<String, dynamic> d) async {
    final collect = (d['amount_to_collect_minor'] as num).toInt();
    if (collect > 0) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text('Delivered #${d['token']}'),
          content: Text(
            'Rider ${d['rider']?['name'] ?? ''} collected ${money(collect)} in cash?\nThe rider owes it until it is handed in.',
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(
              key: const ValueKey('confirm-collected'),
              onPressed: () => Navigator.pop(ctx, true),
              child: Text('Collected ${money(collect)}'),
            ),
          ],
        ),
      );
      if (ok != true) return;
    }
    if (!mounted) return;
    await _move(d, 'delivered', {if (collect > 0) 'collected_minor': collect}, 'Delivered #${d['token']}');
  }

  Future<void> _failed(Map<String, dynamic> d) async {
    final reason = await askReason(
      context,
      'Why did the delivery fail?',
      quick: const ['No answer', 'Wrong address', 'Guest refused', 'Rider accident'],
    );
    if (reason == null || !mounted) return;
    final food = await showDialog<String>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('What happens to the food?'),
        children: [
          for (final f in const [
            ('returned', 'Returned to branch'),
            ('discarded', 'Discarded'),
            ('redeliver', 'Redeliver'),
          ])
            SimpleDialogOption(
              onPressed: () => Navigator.pop(ctx, f.$1),
              child: Padding(padding: const EdgeInsets.symmetric(vertical: 8), child: Text(f.$2)),
            ),
        ],
      ),
    );
    if (food == null || !mounted) return;
    await _move(d, 'failed', {'reason': reason, 'food_disposition': food}, 'Marked failed — decide refund or retry');
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final pack = ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        SectionLabel('Packing (${orders.length})'),
        if (orders.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('Nothing to pack')),
        for (final o in orders) _packCard(t, o),
      ],
    );
    final disp = ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        SectionLabel('Deliveries (${deliveries.length})'),
        if (deliveries.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('No active deliveries')),
        for (final d in deliveries) _deliveryCard(t, d),
      ],
    );
    if (wide) {
      return Row(
        children: [
          Expanded(child: pack),
          const VerticalDivider(width: 1),
          Expanded(child: disp),
        ],
      );
    }
    return DefaultTabController(
      length: 2,
      child: Column(
        children: [
          TabBar(
            tabs: [
              Tab(text: 'Packing (${orders.length})'),
              Tab(text: 'Deliveries (${deliveries.length})'),
            ],
          ),
          Expanded(child: TabBarView(children: [pack, disp])),
        ],
      ),
    );
  }

  Widget _packCard(ThemeData t, Map<String, dynamic> o) {
    final comps = List<Map<String, dynamic>>.from(o['components']);
    final late = o['late'] == true;
    return Card(
      key: ValueKey('pack-${o['token']}'),
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: late ? t.colorScheme.error : t.dividerColor, width: late ? 2 : 1),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('#${o['token']}', style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(width: 8),
                StatusChip(
                  o['service_type'] == 'delivery' ? 'DELIVERY' : o['service_type'].toString().toUpperCase(),
                  icon: o['service_type'] == 'delivery' ? Icons.delivery_dining : Icons.shopping_bag,
                ),
                if (o['channel'] == 'web') ...[const SizedBox(width: 6), const StatusChip('WEB', icon: Icons.public)],
                const Spacer(),
                Text(
                  'Due ${_time(o['promised_at'])}',
                  style: TextStyle(color: late ? t.colorScheme.error : null, fontWeight: FontWeight.w700),
                ),
                if (late) ...[
                  const SizedBox(width: 6),
                  StatusChip('LATE', color: t.colorScheme.error, icon: Icons.alarm),
                ],
              ],
            ),
            if ((o['customer_name'] ?? '').toString().isNotEmpty) Text(o['customer_name']),
            if ((o['notes'] ?? '').toString().isNotEmpty) Text('Note: ${o['notes']}', style: t.textTheme.bodySmall),
            const SizedBox(height: 6),
            for (final c in comps)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  c['done'] == true ? Icons.check_circle : Icons.radio_button_unchecked,
                  color: c['done'] == true ? Colors.green.shade600 : t.colorScheme.outline,
                ),
                title: Text('${c['quantity']} × ${c['label']}'),
                subtitle: Text(
                  '${c['station'] ?? 'not fired'} · ${c['status']}${(c['slot_name'] ?? '').toString().isNotEmpty ? ' · ${c['slot_name']}' : ''}',
                ),
                trailing: c['done'] != true && c['station'] == 'packing' && c['id'] != null
                    ? OutlinedButton(
                        key: ValueKey('ready-${c['id']}'),
                        onPressed: busy ? null : () => _markReady(c),
                        child: const Text('Got it'),
                      )
                    : null,
              ),
            const SizedBox(height: 4),
            Row(
              children: [
                if ((o['balance_minor'] ?? 0) > 0)
                  Text('Due ${money(o['balance_minor'])}', style: t.textTheme.bodySmall),
                const Spacer(),
                FilledButton.icon(
                  key: ValueKey('handover-${o['token']}'),
                  style: o['complete'] == true ? null : FilledButton.styleFrom(backgroundColor: t.colorScheme.error),
                  onPressed: busy ? null : () => _handover(o),
                  icon: Icon(o['complete'] == true ? Icons.done_all : Icons.warning_amber),
                  label: Text(
                    o['complete'] == true
                        ? (o['service_type'] == 'delivery' ? 'Packed for rider' : 'Hand over')
                        : 'Hand over anyway…',
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _deliveryCard(ThemeData t, Map<String, dynamic> d) {
    final status = d['status'] as String;
    final late = d['late'] == true && status != 'delivered';
    return Card(
      key: ValueKey('delivery-${d['token']}'),
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('#${d['token']}', style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
                if ((d['attempt'] as num) > 1) Text('  attempt ${d['attempt']}'),
                const Spacer(),
                StatusChip(
                  status.replaceAll('_', ' ').toUpperCase(),
                  color: status == 'failed' ? t.colorScheme.error : null,
                ),
              ],
            ),
            Text('${d['customer_name'] ?? ''} · ${d['phone'] ?? ''}'),
            Text(
              '${d['address_text'] ?? ''}${(d['landmark'] ?? '').toString().isNotEmpty ? ' (${d['landmark']})' : ''}',
            ),
            Text(
              '${d['zone']} · due ${_time(d['promised_at'])} · '
              '${d['payment'] == 'cod' ? 'collect ${money(d['amount_to_collect_minor'])}' : 'prepaid'}'
              '${d['rider'] != null ? ' · ${d['rider']['name']}' : ''}',
              style: TextStyle(color: late ? t.colorScheme.error : null),
            ),
            if (status == 'failed')
              Text(
                'Failed: ${d['failure_reason']} · food ${d['food_disposition']}. Decide: retry, or refund/void the order.',
                style: TextStyle(color: t.colorScheme.error),
              ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (status == 'awaiting_assignment' || status == 'assigned')
                  OutlinedButton.icon(
                    key: ValueKey('assign-${d['token']}'),
                    onPressed: busy ? null : () => _assign(d),
                    icon: const Icon(Icons.two_wheeler),
                    label: Text(status == 'assigned' ? 'Change rider' : 'Assign rider'),
                  ),
                if (status == 'assigned')
                  FilledButton.icon(
                    key: ValueKey('dispatch-${d['token']}'),
                    onPressed: busy || d['packed'] != true
                        ? null
                        : () => _move(d, 'dispatched', {}, '#${d['token']} out for delivery'),
                    icon: const Icon(Icons.send),
                    label: Text(d['packed'] == true ? 'Dispatch' : 'Pack first'),
                  ),
                if (status == 'dispatched') ...[
                  FilledButton.icon(
                    key: ValueKey('delivered-${d['token']}'),
                    style: FilledButton.styleFrom(backgroundColor: Colors.green.shade700),
                    onPressed: busy ? null : () => _delivered(d),
                    icon: const Icon(Icons.check),
                    label: const Text('Delivered'),
                  ),
                  OutlinedButton(onPressed: busy ? null : () => _failed(d), child: const Text('Failed')),
                ],
                if (status == 'failed' && d['decision_required'] == true)
                  OutlinedButton.icon(
                    onPressed: busy
                        ? null
                        : () => _run('deliveries/${d['id']}/retry', {
                            'reason': 'redeliver',
                          }, 'New delivery attempt created'),
                    icon: const Icon(Icons.replay),
                    label: const Text('Retry delivery'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Rider view: only the rider's own active jobs, with contact details while active.
class RiderJobsScreen extends StatefulWidget {
  const RiderJobsScreen({super.key});

  @override
  State<RiderJobsScreen> createState() => _RiderJobsScreenState();
}

class _RiderJobsScreenState extends State<RiderJobsScreen> {
  List<Map<String, dynamic>> jobs = [];
  Timer? timer;

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
      final r = await context.read<AppState>().api.get('deliveries');
      if (mounted) setState(() => jobs = List<Map<String, dynamic>>.from(r.data['deliveries']));
    } on ApiError catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    if (jobs.isEmpty) return const Center(child: Text('No assigned deliveries'));
    return ListView(
      children: [
        for (final d in jobs)
          Card(
            margin: const EdgeInsets.all(12),
            child: ListTile(
              title: Text('#${d['token']} · ${d['customer_name']} · ${d['phone']}'),
              subtitle: Text('${d['address_text']}\n${d['zone']} · due ${_time(d['promised_at'])}'),
              isThreeLine: true,
              trailing: Text(
                d['payment'] == 'cod' ? 'Collect\n${money(d['amount_to_collect_minor'])}' : 'Prepaid',
                textAlign: TextAlign.end,
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
            ),
          ),
      ],
    );
  }
}
