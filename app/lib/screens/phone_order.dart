import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../widgets/common.dart';

/// Result of the phone order dialog: the body for `POST checks` plus any repeat items to put in the draft.
class PhoneOrder {
  PhoneOrder(this.body, this.repeatItems);
  final Map<String, dynamic> body;
  final List<Map<String, dynamic>> repeatItems;
}

/// Pickup or delivery from a phone call: masked customer lookup, repeat last order at current prices
/// (changes highlighted), delivery zone + address confirmation even on repeat orders.
class PhoneOrderDialog extends StatefulWidget {
  const PhoneOrderDialog({super.key, required this.api, this.initialType = 'pickup'});
  final ApiClient api;
  final String initialType;

  @override
  State<PhoneOrderDialog> createState() => _PhoneOrderDialogState();
}

class _PhoneOrderDialogState extends State<PhoneOrderDialog> {
  late String type = widget.initialType;
  final phone = TextEditingController();
  final name = TextEditingController();
  final address = TextEditingController();
  final landmark = TextEditingController();
  final notes = TextEditingController();
  List<Map<String, dynamic>> matches = [];
  List<Map<String, dynamic>> zones = [];
  Map<String, dynamic>? customer;
  Map<String, dynamic>? repeat;
  String? zoneId;
  String payment = 'cod';
  bool addressConfirmed = false;
  bool searching = false;
  String? error;

  @override
  void initState() {
    super.initState();
    widget.api
        .get('zones')
        .then((r) {
          if (mounted) setState(() => zones = List<Map<String, dynamic>>.from(r.data['zones']));
        })
        .catchError((_) {});
  }

  Future<void> _lookup() async {
    final q = phone.text.replaceAll(RegExp(r'\D'), '');
    if (q.length < 4) return setState(() => error = 'Enter at least 4 digits');
    setState(() {
      searching = true;
      error = null;
    });
    try {
      final r = await widget.api.get('customers', query: {'q': q});
      setState(() => matches = List<Map<String, dynamic>>.from(r.data['customers']));
      if (matches.isEmpty) error = 'No previous orders for this number. Enter the name to continue.';
    } on ApiError catch (e) {
      setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => searching = false);
    }
  }

  Future<void> _pick(Map<String, dynamic> c) async {
    setState(() {
      customer = c;
      name.text = c['name'] ?? '';
      final addrs = List<Map<String, dynamic>>.from(c['addresses'] ?? const []);
      if (addrs.isNotEmpty) {
        address.text = addrs.first['text'] ?? '';
        landmark.text = addrs.first['landmark'] ?? '';
        zoneId = addrs.first['zone']?['id'] ?? zoneId;
        addressConfirmed = false; // repeat orders still confirm the address
      }
    });
    try {
      final r = await widget.api.get('customers/${c['id']}/repeat');
      if (mounted) setState(() => repeat = r.data);
    } on ApiError catch (_) {}
  }

  void _submit({required bool withRepeat}) {
    final digits = phone.text.replaceAll(RegExp(r'\D'), '');
    if (type == 'delivery') {
      if (digits.length < 7) return setState(() => error = 'A delivery needs the phone number');
      if (zoneId == null) return setState(() => error = 'Choose the delivery zone');
      if (address.text.trim().length < 5) return setState(() => error = 'Enter the address');
      if (!addressConfirmed) return setState(() => error = 'Read the address back to the caller and tick Confirmed');
    }
    final body = <String, dynamic>{
      'service_type': type,
      'customer_name': name.text.trim(),
      'customer_phone': phone.text.trim(),
      'notes': notes.text.trim(),
      if (type == 'delivery') ...{
        'zone_id': zoneId,
        'address_text': address.text.trim(),
        'landmark': landmark.text.trim(),
        'payment': payment,
      },
    };
    final items = <Map<String, dynamic>>[];
    if (withRepeat && repeat != null) {
      for (final it in List<Map<String, dynamic>>.from(repeat!['items'])) {
        if (it['status'] == 'unavailable') continue;
        items.add({
          ...Map<String, dynamic>.from(it['spec']),
          'client_line_id': ApiClient.newKey(),
          'quantity': it['quantity'],
          'fire': 'now',
        });
      }
    }
    Navigator.pop(context, PhoneOrder(body, items));
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final repeatItems = List<Map<String, dynamic>>.from(repeat?['items'] ?? const []);
    return AlertDialog(
      title: const Text('Phone order'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SegmentedButton<String>(
                key: const ValueKey('phone-type'),
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'pickup', label: Text('Pickup'), icon: Icon(Icons.shopping_bag_outlined)),
                  ButtonSegment(value: 'delivery', label: Text('Delivery'), icon: Icon(Icons.delivery_dining)),
                ],
                selected: {type},
                onSelectionChanged: (v) => setState(() => type = v.first),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      key: const ValueKey('phone-number'),
                      controller: phone,
                      autofocus: true,
                      keyboardType: TextInputType.phone,
                      decoration: const InputDecoration(labelText: 'Phone'),
                      onSubmitted: (_) => _lookup(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton.tonalIcon(
                    key: const ValueKey('phone-lookup'),
                    onPressed: searching ? null : _lookup,
                    icon: const Icon(Icons.search),
                    label: const Text('Find'),
                  ),
                ],
              ),
              for (final c in matches)
                ListTile(
                  key: ValueKey('customer-${c['id']}'),
                  selected: customer?['id'] == c['id'],
                  leading: const Icon(Icons.person_outline),
                  title: Text('${c['name'].toString().isEmpty ? 'Guest' : c['name']} · ${c['phone_masked']}'),
                  subtitle: Text(
                    '${c['orders']} orders'
                    '${(c['addresses'] as List).isNotEmpty ? ' · ${(c['addresses'] as List).first['text']}' : ''}',
                  ),
                  onTap: () => _pick(c),
                ),
              TextField(
                controller: name,
                decoration: const InputDecoration(labelText: 'Name'),
              ),
              if (type == 'delivery') ...[
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  key: const ValueKey('zone'),
                  initialValue: zoneId,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Delivery zone'),
                  items: [
                    for (final z in zones)
                      DropdownMenuItem(
                        value: z['id'] as String,
                        child: Text('${z['name']} · fee ${money(z['fee_minor'])} · ~${z['eta_minutes']} min'),
                      ),
                  ],
                  onChanged: (v) => setState(() => zoneId = v),
                ),
                TextField(
                  key: const ValueKey('address'),
                  controller: address,
                  maxLines: 2,
                  decoration: const InputDecoration(labelText: 'Address'),
                  onChanged: (_) => setState(() => addressConfirmed = false),
                ),
                TextField(
                  controller: landmark,
                  decoration: const InputDecoration(labelText: 'Landmark (optional)'),
                ),
                CheckboxListTile(
                  key: const ValueKey('address-confirmed'),
                  value: addressConfirmed,
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Address read back and confirmed with the caller'),
                  onChanged: (v) => setState(() => addressConfirmed = v ?? false),
                ),
                SegmentedButton<String>(
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(value: 'cod', label: Text('Cash on delivery')),
                    ButtonSegment(value: 'prepaid', label: Text('Prepaid')),
                  ],
                  selected: {payment},
                  onSelectionChanged: (v) => setState(() => payment = v.first),
                ),
              ],
              TextField(
                controller: notes,
                decoration: const InputDecoration(labelText: 'Notes (optional)'),
              ),
              if (repeat != null && repeatItems.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text(
                  'Last order #${repeat!['check']['token']} · ${repeat!['check']['business_date']}'
                  '${(repeat!['changes'] ?? 0) > 0 ? ' · ${repeat!['changes']} changed' : ''}',
                  style: t.textTheme.titleSmall,
                ),
                for (final it in repeatItems)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      '${it['quantity']} × ${it['name']}',
                      style: it['status'] == 'unavailable'
                          ? const TextStyle(decoration: TextDecoration.lineThrough)
                          : null,
                    ),
                    trailing: switch (it['status']) {
                      'ok' => Text(money(it['new_unit_minor'])),
                      'price_changed' => StatusChip(
                        'was ${money(it['old_unit_minor'])} → ${money(it['new_unit_minor'])}',
                        color: Colors.orange.shade800,
                        icon: Icons.trending_up,
                      ),
                      _ => StatusChip(it['message'] ?? 'unavailable', color: t.colorScheme.error, icon: Icons.block),
                    },
                  ),
              ],
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
        if (repeat != null && repeatItems.any((i) => i['status'] != 'unavailable'))
          OutlinedButton(
            key: const ValueKey('repeat-order'),
            onPressed: () => _submit(withRepeat: true),
            child: const Text('Repeat last order'),
          ),
        FilledButton(
          key: const ValueKey('start-phone-order'),
          onPressed: () => _submit(withRepeat: false),
          child: const Text('Start new order'),
        ),
      ],
    );
  }
}
