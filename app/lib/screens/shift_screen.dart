import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import '../widgets/corrections.dart';

/// Drawer: open with float, blind count, then close (variance needs reason + manager).
class ShiftScreen extends StatefulWidget {
  const ShiftScreen({super.key});

  @override
  State<ShiftScreen> createState() => _ShiftScreenState();
}

class _ShiftScreenState extends State<ShiftScreen> {
  Map<String, dynamic>? shift;
  List<Map<String, dynamic>> riders = [];
  bool loading = true;
  final amount = TextEditingController();
  final reason = TextEditingController();
  final approver = TextEditingController();
  final approverPin = TextEditingController();

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('shifts');
      List<Map<String, dynamic>> rs = [];
      if (app.can('rider.view')) {
        try {
          rs = List<Map<String, dynamic>>.from((await app.api.get('riders')).data['riders']);
        } on ApiError catch (_) {}
      }
      if (mounted) {
        setState(() {
          shift = r.data['shift'];
          riders = rs;
          loading = false;
        });
      }
    } on ApiError catch (e) {
      if (mounted) {
        setState(() => loading = false);
        showError(context, e);
      }
    }
  }

  Future<void> _cmd(String path, Map<String, dynamic> body, String ok) async {
    try {
      await app.api.command(path, body);
      amount.clear();
      if (mounted) showInfo(context, ok);
      await _load();
      await app.refreshBootstrap();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    if (loading) return const Center(child: CircularProgressIndicator());
    final s = shift;
    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: Card(
              child: Padding(padding: const EdgeInsets.all(20), child: s == null ? _openForm(t) : _active(t, s)),
            ),
          ),
        ),
      ],
    );
  }

  Widget _openForm(ThemeData t) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text('No drawer open', style: t.textTheme.headlineSmall),
      const SizedBox(height: 8),
      const Text('Count the opening float and open the drawer before taking cash.'),
      const SizedBox(height: 16),
      TextField(
        key: const ValueKey('float'),
        controller: amount,
        decoration: const InputDecoration(labelText: 'Opening float (Rs)'),
        keyboardType: TextInputType.number,
      ),
      const SizedBox(height: 16),
      FilledButton(
        key: const ValueKey('open-drawer'),
        onPressed: () {
          final v = parseRupees(amount.text);
          if (v == null) return showError(context, 'Enter the float');
          _cmd('shifts', {'opening_float_minor': v}, 'Drawer opened');
        },
        child: const Text('Open drawer'),
      ),
    ],
  );

  Widget _active(ThemeData t, Map<String, dynamic> s) {
    final status = s['status'];
    final summary = s['summary'] as Map<String, dynamic>?;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Text('Drawer ${s['drawer_code']}', style: t.textTheme.headlineSmall),
            const SizedBox(width: 12),
            StatusChip(status.toString().toUpperCase(), color: status == 'open' ? Colors.green : Colors.orange),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          'Opened by ${s['opened_by']} · business day ${s['business_date']} · float ${money(s['opening_float_minor'])}',
        ),
        const Divider(height: 32),
        if (status == 'open') ...[
          Text('Blind count', style: t.textTheme.titleMedium),
          const Text('Count all cash in the drawer. Expected cash is shown only after you submit.'),
          const SizedBox(height: 12),
          TextField(
            key: const ValueKey('counted'),
            controller: amount,
            decoration: const InputDecoration(labelText: 'Counted cash (Rs)'),
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 12),
          FilledButton(
            key: const ValueKey('submit-count'),
            onPressed: () {
              final v = parseRupees(amount.text);
              if (v == null) return showError(context, 'Enter counted cash');
              _cmd('shifts/${s['id']}/count', {'counted_minor': v, 'expected_revision': s['revision']}, 'Count saved');
            },
            child: const Text('Submit count'),
          ),
          if (riders.any((r) => (r['outstanding_minor'] as num) > 0)) ...[
            const Divider(height: 32),
            Text('Rider cash', style: t.textTheme.titleMedium),
            const Text('Cash riders collected on delivery and still hold. Receiving it adds to this drawer once.'),
            for (final r in riders.where((r) => (r['outstanding_minor'] as num) > 0))
              ListTile(
                key: ValueKey('rider-cash-${r['name']}'),
                contentPadding: EdgeInsets.zero,
                title: Text(r['name']),
                subtitle: Text(
                  '${(r['collections'] as List).length} delivery(ies) · oldest '
                  '${(r['collections'] as List).map((c) => c['age_minutes'] as int).fold<int>(0, (a, b) => a > b ? a : b)} min',
                ),
                trailing: FilledButton.tonal(
                  key: ValueKey('remit-${r['name']}'),
                  onPressed: () => _remit(r),
                  child: Text('Receive · owes ${money(r['outstanding_minor'])}'),
                ),
              ),
          ],
          if (app.can('drawer.movement')) ...[
            const Divider(height: 32),
            OutlinedButton.icon(
              onPressed: () => _movement(s),
              icon: const Icon(Icons.outbox),
              label: const Text('Paid out / paid in / drop'),
            ),
          ],
        ] else ...[
          _kv(t, 'Expected cash', money(s['expected_minor'])),
          _kv(t, 'Counted', money(s['counted_minor'])),
          _kv(
            t,
            'Variance',
            money(s['variance_minor']),
            color: (s['variance_minor'] ?? 0) == 0 ? Colors.green : t.colorScheme.error,
          ),
          if (summary != null) ...[
            const SizedBox(height: 8),
            _kv(t, 'Cash sales (net of change)', money(summary['movements']['cash_sale'])),
            _kv(t, 'Paid out', money(summary['movements']['paid_out'])),
            if (summary['refunds'] != null) ...[
              _kv(t, 'Cash refunds paid out', money(summary['refunds']['cash'])),
              if ((summary['refunds']['pending'] ?? 0) > 0)
                Text(
                  '${summary['refunds']['pending']} card refund(s) still pending — resolve before closing',
                  style: TextStyle(color: t.colorScheme.error),
                ),
            ],
            _kv(t, 'Card (not in drawer)', money(summary['tenders']['external_card'])),
            if (summary['riders'] != null) ...[
              _kv(t, 'Rider cash received', money(summary['riders']['remitted_minor'])),
              if ((summary['riders']['outstanding_minor'] ?? 0) > 0)
                for (final r in summary['riders']['by_rider'])
                  _kv(t, '${r['name']} still owes', money(r['outstanding_minor']), color: t.colorScheme.error),
            ],
            if ((summary['open_checks'] as List).isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '${(summary['open_checks'] as List).length} unpaid check(s) carry to the next shift',
                  style: TextStyle(color: t.colorScheme.error),
                ),
              ),
          ],
          const SizedBox(height: 16),
          if ((s['variance_minor'] ?? 0) != 0) ...[
            TextField(
              controller: reason,
              decoration: const InputDecoration(labelText: 'Reason for variance'),
            ),
            if (!app.can('shift.approve_variance')) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: approver,
                      decoration: const InputDecoration(labelText: 'Manager username'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: approverPin,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: 'Manager PIN'),
                    ),
                  ),
                ],
              ),
            ],
            const SizedBox(height: 12),
          ],
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () async {
                    final r = await _askReason('Why reopen the drawer?');
                    if (r != null) _cmd('shifts/${s['id']}/reopen', {'reason': r}, 'Drawer reopened');
                  },
                  child: const Text('Recount'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  key: const ValueKey('close-shift'),
                  onPressed: () => _cmd('shifts/${s['id']}/close', {
                    'expected_revision': s['revision'],
                    'reason': reason.text.trim(),
                    if (approver.text.isNotEmpty)
                      'approval': {'username': approver.text.trim(), 'pin': approverPin.text},
                  }, 'Shift closed'),
                  child: const Text('Close shift'),
                ),
              ),
            ],
          ),
        ],
      ],
    );
  }

  Future<void> _remit(Map<String, dynamic> r) async {
    final ctl = TextEditingController(text: ((r['outstanding_minor'] as num) / 100).toStringAsFixed(0));
    final amt = await showDialog<int>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Cash from ${r['name']}'),
        content: SizedBox(
          width: 360,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Owes ${money(r['outstanding_minor'])}. Count the cash handed in.'),
              TextField(
                key: const ValueKey('remit-amount'),
                controller: ctl,
                autofocus: true,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Cash handed in (Rs)'),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, parseRupees(ctl.text)), child: const Text('Receive')),
        ],
      ),
    );
    if (amt == null || amt <= 0 || !mounted) return;
    final res = await runCorrection(context, app.api, 'riders/${r['id']}/remittances', {'amount_minor': amt});
    if (res != null && mounted) {
      showInfo(context, 'Received ${money(amt)} · ${r['name']} still owes ${money(res.data['outstanding_minor'])}');
    }
    await _load();
  }

  Widget _kv(ThemeData t, String k, String v, {Color? color}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(
      children: [
        Expanded(child: Text(k)),
        Text(
          v,
          style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700, color: color),
        ),
      ],
    ),
  );

  Future<String?> _askReason(String title) {
    final c = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(title),
        content: TextField(controller: c, autofocus: true),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, c.text.trim().isEmpty ? null : c.text.trim()),
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Future<void> _movement(Map<String, dynamic> s) async {
    String kind = 'paid_out';
    final amt = TextEditingController();
    final why = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => StatefulBuilder(
        builder: (context, set) => AlertDialog(
          title: const Text('Drawer movement'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'paid_out', label: Text('Paid out')),
                  ButtonSegment(value: 'paid_in', label: Text('Paid in')),
                  ButtonSegment(value: 'drop', label: Text('Drop')),
                ],
                selected: {kind},
                onSelectionChanged: (v) => set(() => kind = v.first),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: amt,
                decoration: const InputDecoration(labelText: 'Amount (Rs)'),
                keyboardType: TextInputType.number,
              ),
              const SizedBox(height: 12),
              TextField(
                controller: why,
                decoration: const InputDecoration(labelText: 'Reason'),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Record')),
          ],
        ),
      ),
    );
    if (ok != true) return;
    final v = parseRupees(amt.text);
    if (v == null) return;
    await _cmd('shifts/${s['id']}/movements', {'kind': kind, 'amount_minor': v, 'reason': why.text.trim()}, 'Recorded');
  }
}
