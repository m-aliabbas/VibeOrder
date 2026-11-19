import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import 'common.dart';

const _kindLabels = {
  'kitchen': 'KOT',
  'receipt': 'Receipt',
  'bill': 'Bill',
  'refund': 'Refund slip',
  'payer_receipt': 'Payer receipt',
};

String jobTitle(Map<String, dynamic> j) {
  final kind = _kindLabels[j['kind']] ?? j['kind'];
  final parts = <String>[
    if (j['is_copy'] == true) 'COPY',
    kind,
    if (j['ticket_seq'] != null) '#${j['ticket_seq']}',
    if (j['station'] != null) j['station'],
    if (j['ticket_kind'] != null && j['ticket_kind'] != 'original') '(${j['ticket_kind']})',
  ];
  return parts.join(' ');
}

Color jobColor(ThemeData t, String status) => switch (status) {
  'handed_to_spooler' => Colors.green.shade600,
  'outcome_unknown' || 'failed_before_send' => t.colorScheme.error,
  _ => Colors.orange.shade700,
};

class PrintJobTile extends StatelessWidget {
  const PrintJobTile({super.key, required this.job, required this.onTap});
  final Map<String, dynamic> job;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final when = DateFormat.Hm().format(DateTime.parse(job['created_at']).toLocal());
    return ListTile(
      key: ValueKey('job-${job['id']}'),
      minTileHeight: 56,
      leading: Icon(job['kind'] == 'kitchen' ? Icons.soup_kitchen_outlined : Icons.receipt_long_outlined),
      title: Text(jobTitle(job)),
      subtitle: Text(
        '$when · order #${job['check_token'] ?? '-'}'
        '${(job['receipt_no'] ?? '').toString().isNotEmpty ? ' · ${job['receipt_no']}' : ''} · ${job['printer']}'
        '${job['reason'] != null && job['reason'].toString().isNotEmpty ? ' · ${job['reason']}' : ''}',
      ),
      trailing: StatusChip(job['status'].toString().replaceAll('_', ' '), color: jobColor(t, job['status'])),
      onTap: onTap,
    );
  }
}

/// Shows the exact printed text and lets an authorized operator issue a labelled COPY with a reason.
Future<void> showPrintJob(BuildContext context, String jobId) async {
  final app = context.read<AppState>();
  Map<String, dynamic> job;
  try {
    job = (await app.api.get('print-jobs/$jobId')).data;
  } on ApiError catch (e) {
    if (context.mounted) showError(context, e);
    return;
  }
  if (!context.mounted) return;
  await showDialog(
    context: context,
    builder: (ctx) {
      final t = Theme.of(ctx);
      final copies = List<Map<String, dynamic>>.from(job['copies'] ?? const []);
      return AlertDialog(
        title: Text(jobTitle(job)),
        content: SizedBox(
          width: 460,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '${job['status'].toString().replaceAll('_', ' ')} · ${job['printer']}'
                  '${(job['next_action'] ?? '').toString().isNotEmpty ? '\n${job['next_action']}' : ''}',
                  style: TextStyle(color: jobColor(t, job['status'])),
                ),
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: t.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: SelectableText(
                    job['content'] ?? '',
                    key: const ValueKey('job-content'),
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 12, height: 1.25),
                  ),
                ),
                if (copies.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text('Copies printed', style: t.textTheme.labelLarge),
                  for (final c in copies)
                    Text(
                      '${DateFormat.Hm().format(DateTime.parse(c['created_at']).toLocal())} · '
                      '${c['requested_by'] ?? ''}: ${c['reason']} · ${c['status'].toString().replaceAll('_', ' ')}',
                    ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close')),
          if (app.can('print.reprint'))
            FilledButton.icon(
              key: const ValueKey('reprint-copy'),
              onPressed: () async {
                Navigator.pop(ctx);
                await reprintCopy(context, job);
              },
              icon: const Icon(Icons.print),
              label: const Text('Reprint COPY'),
            ),
        ],
      );
    },
  );
}

Future<bool> reprintCopy(BuildContext context, Map<String, dynamic> job) async {
  final app = context.read<AppState>();
  final ctl = TextEditingController();
  final reason = await showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text('Reprint ${jobTitle(job)}'),
      content: SizedBox(
        width: 380,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Check the printer first. The copy is marked COPY with the original ticket number.'),
            Wrap(
              spacing: 8,
              children: [
                for (final q in const ['Not printed', 'Paper jam', 'Kitchen lost ticket', 'Guest asked for copy'])
                  ActionChip(label: Text(q), onPressed: () => Navigator.pop(ctx, q)),
              ],
            ),
            TextField(
              key: const ValueKey('reprint-reason'),
              controller: ctl,
              decoration: const InputDecoration(labelText: 'Reason'),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
        FilledButton(
          onPressed: () => Navigator.pop(ctx, ctl.text.trim().isEmpty ? null : ctl.text.trim()),
          child: const Text('Reprint'),
        ),
      ],
    ),
  );
  if (reason == null) return false;
  try {
    await app.api.command('print-jobs/${job['id']}/reprints', {'reason': reason});
    if (context.mounted) showInfo(context, 'COPY sent to ${job['printer']}');
    return true;
  } on ApiError catch (e) {
    if (context.mounted) showError(context, e);
    return false;
  }
}

/// Bottom sheet with every KOT / receipt for one order.
Future<void> showOrderPrints(BuildContext context, List<Map<String, dynamic>> jobs, {String? title}) {
  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    builder: (ctx) => SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.7),
        child: ListView(
          shrinkWrap: true,
          children: [
            SectionLabel(title ?? 'KOTs & receipts'),
            if (jobs.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('Nothing printed yet')),
            for (final j in jobs.reversed)
              PrintJobTile(
                job: j,
                onTap: () {
                  Navigator.pop(ctx);
                  showPrintJob(context, j['id']);
                },
              ),
          ],
        ),
      ),
    ),
  );
}

/// Branch print log: KOTs and receipts, search by order token, KOT number or receipt number.
class PrintLogScreen extends StatefulWidget {
  const PrintLogScreen({super.key});

  @override
  State<PrintLogScreen> createState() => _PrintLogScreenState();
}

class _PrintLogScreenState extends State<PrintLogScreen> {
  String type = 'all';
  final search = TextEditingController();
  List<Map<String, dynamic>> jobs = [];
  Timer? timer;
  Timer? debounce;

  @override
  void initState() {
    super.initState();
    _load();
    timer = Timer.periodic(const Duration(seconds: 8), (_) => _load());
  }

  @override
  void dispose() {
    timer?.cancel();
    debounce?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await context.read<AppState>().api.get(
        'print-jobs',
        query: {
          if (type == 'kot' || type == 'receipts') 'type': type,
          if (type == 'problems') 'problems': '1',
          if (search.text.trim().isNotEmpty) 'q': search.text.trim(),
        },
      );
      if (mounted) setState(() => jobs = List<Map<String, dynamic>>.from(r.data['jobs']));
    } on ApiError catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
          child: Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              SegmentedButton<String>(
                key: const ValueKey('print-type'),
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'all', label: Text('All')),
                  ButtonSegment(value: 'kot', label: Text('KOTs')),
                  ButtonSegment(value: 'receipts', label: Text('Receipts')),
                  ButtonSegment(value: 'problems', label: Text('Problems')),
                ],
                selected: {type},
                onSelectionChanged: (v) {
                  setState(() => type = v.first);
                  _load();
                },
              ),
              SizedBox(
                width: 300,
                child: TextField(
                  key: const ValueKey('print-search'),
                  controller: search,
                  decoration: const InputDecoration(
                    isDense: true,
                    prefixIcon: Icon(Icons.search),
                    hintText: 'Order token, KOT no. or receipt no.',
                  ),
                  onChanged: (_) {
                    debounce?.cancel();
                    debounce = Timer(const Duration(milliseconds: 300), _load);
                  },
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: jobs.isEmpty
              ? const Center(child: Text('No prints match'))
              : ListView(
                  children: [
                    for (final j in jobs)
                      PrintJobTile(
                        job: j,
                        onTap: () async {
                          await showPrintJob(context, j['id']);
                          await _load();
                        },
                      ),
                  ],
                ),
        ),
      ],
    );
  }
}
