import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/menu.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

/// Size -> crust -> flavour or two halves -> whole/left/right toppings.
/// Left/right always appear as words as well as the visual preview. Price comes from the server.
class PizzaBuilder extends StatefulWidget {
  const PizzaBuilder({
    super.key,
    required this.menu,
    this.initial,
    this.fixedSize,
    this.allowedProducts,
    this.inDeal = false,
  });

  final MenuView menu;
  final Map<String, dynamic>? initial;
  final String? fixedSize;
  final Set<String>? allowedProducts;
  final bool inDeal;

  static Future<Map<String, dynamic>?> open(
    BuildContext context,
    MenuView menu, {
    Map<String, dynamic>? initial,
    String? fixedSize,
    Set<String>? allowedProducts,
    bool inDeal = false,
  }) {
    return Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => PizzaBuilder(
          menu: menu,
          initial: initial,
          fixedSize: fixedSize,
          allowedProducts: allowedProducts,
          inDeal: inDeal,
        ),
      ),
    );
  }

  @override
  State<PizzaBuilder> createState() => _PizzaBuilderState();
}

class _PizzaBuilderState extends State<PizzaBuilder> {
  late bool halfAndHalf;
  late String size;
  String? flavour;
  String? left;
  String? right;
  String? crust;
  final Map<String, String> toppings = {}; // option code -> whole|left|right
  int quantity = 1;
  final notes = TextEditingController();

  int? price;
  String? priceError;
  Timer? _debounce;
  int _req = 0;

  MenuView get m => widget.menu;

  @override
  void initState() {
    super.initState();
    final init = widget.initial;
    final sizes = m.sizes();
    size = widget.fixedSize ?? init?['variant'] ?? (sizes.isNotEmpty ? sizes.last['code'] : 'large');
    halfAndHalf = init?['product'] == 'mixed-pizza';
    if (init != null) {
      if (halfAndHalf) {
        left = init['halves']?['left'];
        right = init['halves']?['right'];
      } else {
        flavour = init['product'];
      }
      crust = init['crust'];
      for (final o in List<Map<String, dynamic>>.from(init['options'] ?? const [])) {
        toppings[o['id']] = o['side'];
      }
      quantity = init['quantity'] ?? 1;
      notes.text = init['notes'] ?? '';
    }
    final mixed = m.productByCode('mixed-pizza');
    final crustGroup = mixed == null ? null : m.groupsFor(mixed, 'crust').firstOrNull;
    crust ??= crustGroup == null
        ? null
        : List<Map<String, dynamic>>.from(
            crustGroup['options'],
          ).firstWhere((o) => o['default'] == true, orElse: () => crustGroup['options'][0])['code'];
    _schedulePrice();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  bool get complete => halfAndHalf ? (left != null && right != null) : flavour != null;

  Map<String, dynamic> buildItem() => {
    'client_line_id': widget.initial?['client_line_id'] ?? ApiClient.newKey(),
    'product': halfAndHalf ? 'mixed-pizza' : flavour,
    'variant': size,
    if (!widget.inDeal) 'quantity': quantity,
    'crust': crust,
    if (halfAndHalf) 'halves': {'left': left, 'right': right},
    'options': [
      for (final e in toppings.entries) {'id': e.key, 'side': e.value},
    ],
    if (notes.text.trim().isNotEmpty) 'notes': notes.text.trim(),
  };

  void _schedulePrice() {
    _debounce?.cancel();
    if (!complete || widget.inDeal) {
      setState(() => price = null);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 250), () async {
      final req = ++_req;
      try {
        final r = await context.read<AppState>().api.post('quotes', {
          'preview': true,
          'items': [buildItem()],
        });
        if (mounted && req == _req) {
          setState(() {
            price = (r.data['total_minor'] as num).toInt();
            priceError = null;
          });
        }
      } on ApiError catch (e) {
        if (mounted && req == _req) {
          setState(() {
            price = null;
            priceError = e.message;
          });
        }
      }
    });
  }

  void _update(VoidCallback fn) {
    setState(fn);
    _schedulePrice();
  }

  List<Map<String, dynamic>> get _flavours =>
      m.flavours.where((f) => widget.allowedProducts == null || widget.allowedProducts!.contains(f['code'])).toList();

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final mixed = m.productByCode('mixed-pizza');
    final allowMixed =
        mixed != null && (widget.allowedProducts == null || widget.allowedProducts!.contains('mixed-pizza'));
    final crustGroup = mixed == null ? null : m.groupsFor(mixed, 'crust').firstOrNull;
    final topGroup = mixed == null ? null : m.groupsFor(mixed, 'topping').firstOrNull;
    final wide = MediaQuery.sizeOf(context).width > 900;

    final form = ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        if (widget.fixedSize == null) ...[
          const SectionLabel('1 · Size'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: SegmentedButton<String>(
              segments: [for (final s in m.sizes()) ButtonSegment(value: s['code'], label: Text(s['name']))],
              selected: {size},
              onSelectionChanged: (v) => _update(() => size = v.first),
            ),
          ),
        ],
        SectionLabel('2 · Crust'),
        if (crustGroup != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final o in List<Map<String, dynamic>>.from(crustGroup['options']))
                  ChoiceChip(
                    key: ValueKey('crust-${o['code']}'),
                    label: Text('${o['name']}${_extra(m.optionPrice(o['code'], size).$1)}'),
                    selected: crust == o['code'],
                    onSelected: o['available'] == false ? null : (_) => _update(() => crust = o['code']),
                  ),
              ],
            ),
          ),
        SectionLabel(
          '3 · Flavour',
          trailing: allowMixed
              ? SegmentedButton<bool>(
                  segments: const [
                    ButtonSegment(value: false, label: Text('One flavour')),
                    ButtonSegment(value: true, label: Text('Half & half')),
                  ],
                  selected: {halfAndHalf},
                  onSelectionChanged: (v) => _update(() => halfAndHalf = v.first),
                )
              : null,
        ),
        if (!halfAndHalf)
          _flavourChips('flavour', flavour, (c) => _update(() => flavour = c))
        else ...[
          _sideHeader(t, 'LEFT HALF'),
          _flavourChips('left', left, (c) => _update(() => left = c)),
          _sideHeader(t, 'RIGHT HALF'),
          _flavourChips('right', right, (c) => _update(() => right = c)),
        ],
        if (topGroup != null) ...[
          SectionLabel('4 · Extra toppings (max ${topGroup['max']} per half)'),
          for (final o in List<Map<String, dynamic>>.from(topGroup['options'])) _toppingRow(t, o),
        ],
        if (!widget.inDeal) ...[
          const SectionLabel('Quantity & note'),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                IconButton.filledTonal(
                  onPressed: quantity > 1 ? () => _update(() => quantity--) : null,
                  icon: const Icon(Icons.remove),
                ),
                SizedBox(
                  width: 48,
                  child: Text('$quantity', textAlign: TextAlign.center, style: t.textTheme.titleLarge),
                ),
                IconButton.filledTonal(onPressed: () => _update(() => quantity++), icon: const Icon(Icons.add)),
                const SizedBox(width: 16),
                Expanded(
                  child: TextField(
                    controller: notes,
                    decoration: const InputDecoration(labelText: 'Kitchen note / allergy', isDense: true),
                    maxLength: 200,
                    onChanged: (_) => _schedulePrice(),
                  ),
                ),
              ],
            ),
          ),
        ],
      ],
    );

    final preview = _PizzaPreview(
      halfAndHalf: halfAndHalf,
      left: halfAndHalf
          ? (left == null ? null : m.productName(left!))
          : (flavour == null ? null : m.productName(flavour!)),
      right: halfAndHalf ? (right == null ? null : m.productName(right!)) : null,
      leftToppings: [
        for (final e in toppings.entries)
          if (e.value != 'right') m.optionName(e.key),
      ],
      rightToppings: [
        for (final e in toppings.entries)
          if (e.value != 'left') m.optionName(e.key),
      ],
    );

    return Scaffold(
      appBar: AppBar(title: Text(widget.inDeal ? 'Deal pizza (${_sizeName()})' : 'Build pizza')),
      body: wide
          ? Row(
              children: [
                Expanded(child: form),
                SizedBox(
                  width: 340,
                  child: Padding(padding: const EdgeInsets.all(16), child: preview),
                ),
              ],
            )
          : Column(
              children: [
                SizedBox(height: 170, child: preview),
                Expanded(child: form),
              ],
            ),
      bottomNavigationBar: SafeArea(
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          decoration: BoxDecoration(
            border: Border(top: BorderSide(color: t.colorScheme.outlineVariant)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      complete ? describeItem(m, buildItem()) : 'Choose ${halfAndHalf ? 'both halves' : 'a flavour'}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    if (priceError != null)
                      Text(priceError!, style: TextStyle(color: t.colorScheme.error), maxLines: 2)
                    else if (!widget.inDeal)
                      Text(
                        price == null ? '—' : money(price),
                        key: const ValueKey('builder-price'),
                        style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              FilledButton.icon(
                key: const ValueKey('builder-confirm'),
                onPressed: complete && priceError == null ? () => Navigator.pop(context, buildItem()) : null,
                icon: const Icon(Icons.check),
                label: Text(
                  widget.initial?['client_line_id'] == null
                      ? (widget.inDeal ? 'Use in deal' : 'Add to order')
                      : 'Update',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _sizeName() => m.sizes().firstWhere((s) => s['code'] == size, orElse: () => {'name': size})['name'];

  String _extra(int minor) => minor == 0 ? '' : '  +${money(minor)}';

  Widget _sideHeader(ThemeData t, String text) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
    child: Text(
      text,
      style: t.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800, color: t.colorScheme.primary),
    ),
  );

  Widget _flavourChips(String keyPrefix, String? selected, void Function(String) onPick) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final f in _flavours)
            ChoiceChip(
              key: ValueKey('$keyPrefix-${f['code']}'),
              showCheckmark: false,
              label: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(f['name'] + (f['available'] == false ? ' (sold out)' : '')),
                  if ((f['name_local'] ?? '').toString().isNotEmpty)
                    Text(f['name_local'], style: const TextStyle(fontSize: 12)),
                  if (!widget.inDeal)
                    Text(money(m.variantPrice(f['code'], size)), style: const TextStyle(fontSize: 12)),
                ],
              ),
              selected: selected == f['code'],
              onSelected: f['available'] == false ? null : (_) => onPick(f['code']),
            ),
        ],
      ),
    );
  }

  Widget _toppingRow(ThemeData t, Map<String, dynamic> o) {
    final code = o['code'] as String;
    final (whole, half) = m.optionPrice(code, size);
    final current = toppings[code] ?? 'none';
    final soldOut = o['available'] == false;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  o['name'] + (soldOut ? ' — sold out' : ''),
                  style: TextStyle(fontWeight: FontWeight.w600, color: soldOut ? t.disabledColor : null),
                ),
                Text('Whole ${money(whole)} · Half ${money(half)}', style: t.textTheme.bodySmall),
              ],
            ),
          ),
          SegmentedButton<String>(
            key: ValueKey('topping-$code'),
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: 'none', label: Text('None')),
              ButtonSegment(value: 'left', label: Text('Left')),
              ButtonSegment(value: 'whole', label: Text('Whole')),
              ButtonSegment(value: 'right', label: Text('Right')),
            ],
            selected: {current},
            onSelectionChanged: soldOut
                ? null
                : (v) => _update(() {
                    if (v.first == 'none') {
                      toppings.remove(code);
                    } else {
                      toppings[code] = v.first;
                    }
                  }),
          ),
        ],
      ),
    );
  }
}

/// Visual half/half preview, always labelled LEFT / RIGHT in words.
class _PizzaPreview extends StatelessWidget {
  const _PizzaPreview({
    required this.halfAndHalf,
    this.left,
    this.right,
    this.leftToppings = const [],
    this.rightToppings = const [],
  });

  final bool halfAndHalf;
  final String? left;
  final String? right;
  final List<String> leftToppings;
  final List<String> rightToppings;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    Widget half(String side, String? name, List<String> tops, BorderRadius r, Color c) => Expanded(
      child: Container(
        decoration: BoxDecoration(color: c, borderRadius: r),
        padding: const EdgeInsets.all(10),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              side,
              style: const TextStyle(fontWeight: FontWeight.w900, letterSpacing: 1.2, color: Colors.white),
            ),
            Text(
              name ?? 'choose',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
            ),
            for (final x in tops) Text('+ $x', style: const TextStyle(color: Colors.white, fontSize: 12)),
          ],
        ),
      ),
    );
    return Center(
      child: AspectRatio(
        aspectRatio: 1,
        child: Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: const Color(0xFFE9B66B),
            border: Border.all(color: const Color(0xFFB9772E), width: 6),
          ),
          child: ClipOval(
            child: halfAndHalf
                ? Row(
                    children: [
                      half('LEFT', left, leftToppings, BorderRadius.zero, const Color(0xFFC0392B)),
                      Container(width: 3, color: t.colorScheme.surface),
                      half('RIGHT', right, rightToppings, BorderRadius.zero, const Color(0xFFD35400)),
                    ],
                  )
                : Container(
                    color: const Color(0xFFC0392B),
                    alignment: Alignment.center,
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(
                          left ?? 'Choose flavour',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16),
                        ),
                        for (final x in leftToppings.where(rightToppings.contains))
                          Text('+ $x (whole)', style: const TextStyle(color: Colors.white)),
                        for (final x in leftToppings.where((e) => !rightToppings.contains(e)))
                          Text('+ $x (LEFT)', style: const TextStyle(color: Colors.white)),
                        for (final x in rightToppings.where((e) => !leftToppings.contains(e)))
                          Text('+ $x (RIGHT)', style: const TextStyle(color: Colors.white)),
                      ],
                    ),
                  ),
          ),
        ),
      ),
    );
  }
}
