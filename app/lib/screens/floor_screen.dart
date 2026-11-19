import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';
import 'order_screen.dart';

/// Table occupancy with waiter, guests and running total. Polling is the MVP transport.
class FloorScreen extends StatefulWidget {
  const FloorScreen({super.key});

  @override
  State<FloorScreen> createState() => _FloorScreenState();
}

class _FloorScreenState extends State<FloorScreen> {
  Map<String, dynamic>? floor;
  String? error;
  Timer? timer;
  bool listView = false;
  String query = '';
  int areaIndex = 0;

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
      final r = await context.read<AppState>().api.get('floor');
      if (mounted) {
        setState(() {
          floor = r.data;
          error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> _openTable(Map<String, dynamic> table) async {
    final app = context.read<AppState>();
    if (table['occupied'] == true) {
      return _go(table['check']['id'], 'Table ${table['label']}');
    }
    final guests = await showDialog<int>(
      context: context,
      builder: (_) => _GuestDialog(table: table['label']),
    );
    if (guests == null || !mounted) return;
    try {
      final r = await app.api.command('checks', {
        'service_type': 'dine_in',
        'table_id': table['id'],
        'guest_count': guests,
      });
      await _go(r.data['check_id'], 'Table ${table['label']}');
    } on ApiError catch (e) {
      if (e.code == 'table_occupied' && e.body['check_id'] != null) {
        // Another waiter opened it first: recover the existing check instead of creating a second one.
        if (mounted) showInfo(context, 'Table was just opened by someone else. Opening that check.');
        await _go(e.body['check_id'], 'Table ${table['label']}');
      } else if (mounted) {
        showError(context, e);
      }
    }
  }

  Future<void> _go(String checkId, String title) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => OrderScreen(checkId: checkId, title: title),
      ),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    if (floor == null) {
      return Center(child: error != null ? Text(error!) : const CircularProgressIndicator());
    }
    final areas = List<Map<String, dynamic>>.from(floor!['areas']);
    if (areaIndex >= areas.length) areaIndex = 0;
    final tables = listView || query.isNotEmpty
        ? [for (final a in areas) ...List<Map<String, dynamic>>.from(a['tables'])]
        : List<Map<String, dynamic>>.from(areas.isEmpty ? [] : areas[areaIndex]['tables']);
    final filtered = tables
        .where((tb) => query.isEmpty || tb['label'].toString().toLowerCase().contains(query.toLowerCase()))
        .toList();

    return Column(
      children: [
        if (error != null)
          MaterialBanner(
            content: Text('Connection problem: $error. Showing last known floor.'),
            leading: const Icon(Icons.wifi_off),
            actions: [TextButton(onPressed: _load, child: const Text('Retry'))],
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: SegmentedButton<int>(
                    segments: [
                      for (var i = 0; i < areas.length; i++) ButtonSegment(value: i, label: Text(areas[i]['name'])),
                    ],
                    selected: {areaIndex},
                    onSelectionChanged: listView ? null : (s) => setState(() => areaIndex = s.first),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 160,
                child: TextField(
                  decoration: const InputDecoration(
                    isDense: true,
                    hintText: 'Find table',
                    prefixIcon: Icon(Icons.search),
                  ),
                  onChanged: (v) => setState(() => query = v),
                ),
              ),
              IconButton(
                tooltip: listView ? 'Grid view' : 'List view',
                onPressed: () => setState(() => listView = !listView),
                icon: Icon(listView ? Icons.grid_view : Icons.view_list),
              ),
            ],
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: listView
                ? ListView(
                    children: [
                      for (final tb in filtered)
                        ListTile(
                          key: ValueKey('table-${tb['label']}'),
                          minTileHeight: 64,
                          leading: CircleAvatar(
                            backgroundColor: tb['occupied']
                                ? t.colorScheme.primaryContainer
                                : t.colorScheme.surfaceContainerHighest,
                            child: Text(tb['label']),
                          ),
                          title: Text('Table ${tb['label']}'),
                          subtitle: Text(
                            tb['occupied']
                                ? '${tb['check']['waiter']} · ${tb['check']['guest_count']} guests · #${tb['check']['token']}'
                                : 'Free · ${tb['seats']} seats',
                          ),
                          trailing: tb['occupied'] ? Text(money(tb['check']['total_minor'])) : null,
                          onTap: () => _openTable(tb),
                        ),
                    ],
                  )
                : GridView.extent(
                    padding: const EdgeInsets.all(16),
                    maxCrossAxisExtent: 200,
                    mainAxisSpacing: 12,
                    crossAxisSpacing: 12,
                    childAspectRatio: 1.15,
                    children: [for (final tb in filtered) _TableTile(table: tb, onTap: () => _openTable(tb))],
                  ),
          ),
        ),
      ],
    );
  }
}

class _TableTile extends StatelessWidget {
  const _TableTile({required this.table, required this.onTap});
  final Map<String, dynamic> table;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final occ = table['occupied'] == true;
    final chk = table['check'];
    final bg = occ ? t.colorScheme.primaryContainer : t.colorScheme.surfaceContainerLow;
    final fg = occ ? t.colorScheme.onPrimaryContainer : t.colorScheme.onSurface;
    return Material(
      color: bg,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        key: ValueKey('table-${table['label']}'),
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(
                    table['label'],
                    style: t.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800, color: fg),
                  ),
                  const Spacer(),
                  if (occ && chk['bill_requested'] == true)
                    const StatusChip('BILL', icon: Icons.receipt_long, color: Colors.deepOrange),
                ],
              ),
              const Spacer(),
              if (occ) ...[
                Text(
                  chk['waiter'],
                  style: TextStyle(color: fg),
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  '${chk['guest_count']} guests · #${chk['token']}',
                  style: t.textTheme.bodySmall?.copyWith(color: fg),
                ),
                Text(
                  money(chk['total_minor']),
                  style: TextStyle(fontWeight: FontWeight.w700, color: fg),
                ),
              ] else
                Text(
                  'Free · ${table['seats']} seats',
                  style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _GuestDialog extends StatefulWidget {
  const _GuestDialog({required this.table});
  final String table;

  @override
  State<_GuestDialog> createState() => _GuestDialogState();
}

class _GuestDialogState extends State<_GuestDialog> {
  int guests = 2;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Open table ${widget.table}'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('Guests'),
          const SizedBox(height: 8),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton.filledTonal(
                iconSize: 32,
                onPressed: guests > 1 ? () => setState(() => guests--) : null,
                icon: const Icon(Icons.remove),
              ),
              SizedBox(
                width: 72,
                child: Text('$guests', textAlign: TextAlign.center, style: Theme.of(context).textTheme.displaySmall),
              ),
              IconButton.filledTonal(
                iconSize: 32,
                onPressed: () => setState(() => guests++),
                icon: const Icon(Icons.add),
              ),
            ],
          ),
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('open-check'),
          onPressed: () => Navigator.pop(context, guests),
          child: const Text('Open check'),
        ),
      ],
    );
  }
}
