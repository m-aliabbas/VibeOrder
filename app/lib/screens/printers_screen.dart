import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

/// Manager: thermal printers (USB on the counter PC, network, or test file) and the receipt header / footer.
class PrintersScreen extends StatefulWidget {
  const PrintersScreen({super.key});

  @override
  State<PrintersScreen> createState() => _PrintersScreenState();
}

class _PrintersScreenState extends State<PrintersScreen> {
  Map<String, dynamic>? data;
  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await app.api.get('printers');
      if (mounted) setState(() => data = r.data);
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _edit([Map<String, dynamic>? p]) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => _PrinterDialog(printer: p, options: data!),
    );
    if (ok == true) _load();
  }

  Future<void> _test(Map<String, dynamic> p) async {
    try {
      await app.api.command('printers/${p['id']}/test', {});
      if (mounted) showInfo(context, 'Test page sent to ${p['name']}');
      Future.delayed(const Duration(seconds: 3), _load);
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final d = data;
    return Scaffold(
      appBar: AppBar(title: const Text('Printers & receipt')),
      floatingActionButton: d == null
          ? null
          : FloatingActionButton.extended(
              key: const ValueKey('add-printer'),
              onPressed: () => _edit(),
              icon: const Icon(Icons.add),
              label: const Text('Add printer'),
            ),
      body: d == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 96),
              children: [
                Text(
                  'Thermal 80 mm printers. KOTs go to the printer of each kitchen station; bills and receipts go to '
                  'the receipt printer. A USB printer must be installed in Windows on the PC running VibeOrder.',
                  style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: 12),
                for (final p in d['printers'])
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Card(
                      child: ListTile(
                        key: ValueKey('printer-${p['name']}'),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                        leading: Icon(
                          p['purpose'] == 'kitchen' ? Icons.soup_kitchen_outlined : Icons.receipt_long_outlined,
                          color: p['last_error'] != '' ? t.colorScheme.error : t.colorScheme.primary,
                        ),
                        title: Text(p['name'], style: const TextStyle(fontWeight: FontWeight.w700)),
                        subtitle: Text(
                          [
                            p['purpose'] == 'kitchen' ? 'KOT · ${p['station'] ?? 'all stations'}' : 'Bills & receipts',
                            _transportLabel(p['transport'], p['address']),
                            if (p['last_error'] != '') 'Problem: ${p['last_error']}',
                            if (p['is_active'] != true) 'switched off',
                          ].join(' · '),
                        ),
                        onTap: () => _edit(Map<String, dynamic>.from(p)),
                        trailing: OutlinedButton.icon(
                          onPressed: () => _test(Map<String, dynamic>.from(p)),
                          icon: const Icon(Icons.print_outlined, size: 18),
                          label: const Text('Test'),
                        ),
                      ),
                    ),
                  ),
                const SizedBox(height: 12),
                _ReceiptSettings(initial: Map<String, dynamic>.from(d['receipt']), onSaved: _load),
              ],
            ),
    );
  }

  String _transportLabel(String transport, String address) => switch (transport) {
    'system' => 'USB: $address',
    'tcp' => 'Network: $address',
    _ => 'Test file: $address',
  };
}

class _PrinterDialog extends StatefulWidget {
  const _PrinterDialog({required this.printer, required this.options});
  final Map<String, dynamic>? printer;
  final Map<String, dynamic> options;

  @override
  State<_PrinterDialog> createState() => _PrinterDialogState();
}

class _PrinterDialogState extends State<_PrinterDialog> {
  late final TextEditingController name;
  late final TextEditingController address;
  late String purpose;
  late String transport;
  String? station;
  late int width;
  late bool active;
  String? error;
  bool busy = false;

  List<String> get installed => List<String>.from(widget.options['installed'] ?? const []);

  @override
  void initState() {
    super.initState();
    final p = widget.printer;
    name = TextEditingController(text: p?['name'] ?? '');
    address = TextEditingController(text: p?['address'] ?? '');
    purpose = p?['purpose'] ?? 'receipt';
    transport = p?['transport'] ?? 'system';
    station = p?['station_id'];
    width = p?['width_chars'] ?? 48;
    active = p?['is_active'] ?? true;
  }

  Future<void> _save() async {
    setState(() {
      busy = true;
      error = null;
    });
    final body = {
      'name': name.text.trim(),
      'purpose': purpose,
      'transport': transport,
      'address': address.text.trim(),
      'station_id': purpose == 'kitchen' ? station : null,
      'width_chars': width,
      'is_active': active,
    };
    try {
      final api = context.read<AppState>().api;
      if (widget.printer == null) {
        await api.command('printers', body);
      } else {
        await api.command('printers/${widget.printer!['id']}', body);
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final stations = List<Map<String, dynamic>>.from(widget.options['stations'] ?? const []);
    return AlertDialog(
      title: Text(widget.printer == null ? 'Add printer' : 'Edit ${widget.printer!['name']}'),
      content: SizedBox(
        width: 460,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                key: const ValueKey('printer-name'),
                controller: name,
                decoration: const InputDecoration(labelText: 'Name', hintText: 'e.g. Counter printer'),
              ),
              const SizedBox(height: 12),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'receipt', label: Text('Bills & receipts')),
                  ButtonSegment(value: 'kitchen', label: Text('Kitchen (KOT)')),
                ],
                selected: {purpose},
                onSelectionChanged: (s) => setState(() => purpose = s.first),
              ),
              if (purpose == 'kitchen') ...[
                const SizedBox(height: 12),
                DropdownButtonFormField<String?>(
                  isExpanded: true,
                  initialValue: station,
                  decoration: const InputDecoration(labelText: 'Station'),
                  items: [
                    const DropdownMenuItem(value: null, child: Text('All stations without their own printer')),
                    for (final s in stations)
                      DropdownMenuItem(value: s['id'] as String, child: Text(s['name'] as String)),
                  ],
                  onChanged: (v) => setState(() => station = v),
                ),
              ],
              const SizedBox(height: 16),
              Text('Connected by', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                children: [
                  for (final o in const [
                    ('system', 'USB / Windows'),
                    ('tcp', 'Network (IP)'),
                    ('spool_file', 'Test file'),
                  ])
                    ChoiceChip(
                      label: Text(o.$2),
                      selected: transport == o.$1,
                      onSelected: (_) => setState(() => transport = o.$1),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              if (transport == 'system' && installed.isNotEmpty)
                DropdownButtonFormField<String>(
                  key: const ValueKey('printer-installed'),
                  isExpanded: true,
                  initialValue: installed.contains(address.text) ? address.text : null,
                  decoration: const InputDecoration(labelText: 'Printer installed on this PC'),
                  items: [for (final n in installed) DropdownMenuItem(value: n, child: Text(n))],
                  onChanged: (v) => setState(() => address.text = v ?? ''),
                )
              else
                TextField(
                  key: const ValueKey('printer-address'),
                  controller: address,
                  decoration: InputDecoration(
                    labelText: switch (transport) {
                      'system' => 'Windows printer name',
                      'tcp' => 'IP address',
                      _ => 'Folder name',
                    },
                    hintText: switch (transport) {
                      'system' => 'e.g. EPSON TM-T88V Receipt',
                      'tcp' => 'e.g. 192.168.1.50',
                      _ => 'e.g. counter-receipt',
                    },
                    helperText: transport == 'system'
                        ? 'No installed printers found. Install the printer driver in Windows first.'
                        : null,
                  ),
                ),
              const SizedBox(height: 12),
              DropdownButtonFormField<int>(
                isExpanded: true,
                initialValue: width,
                decoration: const InputDecoration(labelText: 'Paper'),
                items: const [
                  DropdownMenuItem(value: 48, child: Text('80 mm (48 characters)')),
                  DropdownMenuItem(value: 42, child: Text('80 mm, large font (42 characters)')),
                  DropdownMenuItem(value: 32, child: Text('58 mm (32 characters)')),
                ],
                onChanged: (v) => setState(() => width = v ?? 48),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('In use'),
                value: active,
                onChanged: (v) => setState(() => active = v),
              ),
              if (error != null) Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(key: const ValueKey('printer-save'), onPressed: busy ? null : _save, child: const Text('Save')),
      ],
    );
  }
}

class _ReceiptSettings extends StatefulWidget {
  const _ReceiptSettings({required this.initial, required this.onSaved});
  final Map<String, dynamic> initial;
  final VoidCallback onSaved;

  @override
  State<_ReceiptSettings> createState() => _ReceiptSettingsState();
}

class _ReceiptSettingsState extends State<_ReceiptSettings> {
  late final Map<String, TextEditingController> c = {
    for (final k in ['address', 'phone', 'tax_number', 'receipt_footer'])
      k: TextEditingController(text: widget.initial[k] ?? ''),
  };

  Future<void> _save() async {
    try {
      await context.read<AppState>().api.command('receipt-settings', {
        for (final e in c.entries) e.key: e.value.text.trim(),
      });
      if (mounted) showInfo(context, 'Receipt header saved');
      widget.onSaved();
    } on ApiError catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Printed on every bill and receipt', style: t.textTheme.titleMedium),
            Text(
              '${widget.initial['business_name']} · ${widget.initial['branch_name']}',
              style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: c['address'],
              decoration: const InputDecoration(labelText: 'Address'),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: c['phone'],
                    decoration: const InputDecoration(labelText: 'Phone'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: TextField(
                    controller: c['tax_number'],
                    decoration: const InputDecoration(labelText: 'NTN / STRN'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            TextField(
              controller: c['receipt_footer'],
              decoration: const InputDecoration(labelText: 'Last line', hintText: 'Thank you! Please visit again.'),
            ),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton(key: const ValueKey('receipt-save'), onPressed: _save, child: const Text('Save')),
            ),
          ],
        ),
      ),
    );
  }
}
