/// Read-only view over the branch menu payload (GET /menu).
class MenuView {
  MenuView(this.raw) {
    for (final c in categories) {
      for (final p in List<Map<String, dynamic>>.from(c['products'])) {
        products[p['code']] = p;
      }
    }
    for (final g in List<Map<String, dynamic>>.from(raw['option_groups'] ?? const [])) {
      groups[g['code']] = g;
      for (final o in List<Map<String, dynamic>>.from(g['options'])) {
        options[o['code']] = {...o, 'group': g['code']};
      }
    }
  }

  final Map<String, dynamic> raw;
  final Map<String, Map<String, dynamic>> products = {};
  final Map<String, Map<String, dynamic>> groups = {};
  final Map<String, Map<String, dynamic>> options = {};

  List<Map<String, dynamic>> get categories => List<Map<String, dynamic>>.from(raw['categories'] ?? const []);
  String? get releaseId => raw['release']?['id'];

  List<Map<String, dynamic>> get flavours => products.values.where((p) => p['kind'] == 'pizza').toList();

  Map<String, dynamic>? productByCode(String code) => products[code];

  List<Map<String, dynamic>> groupsFor(Map<String, dynamic> product, String kind) => [
    for (final code in List<String>.from(product['option_groups'] ?? const []))
      if (groups[code]?['kind'] == kind) groups[code]!,
  ];

  /// Option price for a size: (whole, half) in minor units.
  (int, int) optionPrice(String optionCode, String size) {
    final prices = List<Map<String, dynamic>>.from(options[optionCode]?['prices'] ?? const []);
    final p = prices.firstWhere(
      (x) => x['size'] == size,
      orElse: () => prices.firstWhere((x) => x['size'] == null, orElse: () => {'whole_minor': 0, 'half_minor': 0}),
    );
    return ((p['whole_minor'] as num).toInt(), (p['half_minor'] as num).toInt());
  }

  int variantPrice(String productCode, String variant) {
    final p = products[productCode];
    if (p == null) return 0;
    final v = List<Map<String, dynamic>>.from(p['variants']).where((v) => v['code'] == variant);
    return v.isEmpty ? 0 : (v.first['price_minor'] as num).toInt();
  }

  List<Map<String, dynamic>> sizes() {
    final f = flavours;
    if (f.isEmpty) return [];
    return List<Map<String, dynamic>>.from(f.first['variants']);
  }

  String optionName(String code) => options[code]?['name'] ?? code;
  String productName(String code) => products[code]?['name'] ?? code;
}

/// Plain-language description of a draft item for waiter and cashier screens.
String describeItem(MenuView m, Map<String, dynamic> it) {
  final p = m.productByCode(it['product']);
  if (p == null) return it['product'];
  final parts = <String>[];
  if (p['kind'] == 'deal') {
    for (final s in List<Map<String, dynamic>>.from(it['selections'] ?? const [])) {
      parts.add(describeItem(m, s));
    }
    return parts.join(' + ');
  }
  final variants = List<Map<String, dynamic>>.from(p['variants']);
  // Half & half has no own variants: take the size name from a flavour.
  final sizeSource = p['kind'] == 'mixed_pizza' ? m.sizes() : variants;
  final v = sizeSource.where((x) => x['code'] == it['variant']).toList();
  final sizeName = v.isEmpty ? '' : v.first['name'];
  if (p['kind'] == 'mixed_pizza') {
    final h = it['halves'] ?? {};
    parts.add('$sizeName · LEFT ${m.productName(h['left'] ?? '?')} / RIGHT ${m.productName(h['right'] ?? '?')}');
  } else if (p['kind'] == 'pizza') {
    parts.add('$sizeName ${p['name']}');
  } else {
    parts.add(variants.length > 1 ? '$sizeName ${p['name']}' : p['name']);
  }
  if (it['crust'] != null) parts.add(m.optionName(it['crust']));
  for (final o in List<Map<String, dynamic>>.from(it['options'] ?? const [])) {
    final side = o['side'] == 'whole' ? '' : ' (${o['side'].toString().toUpperCase()} half)';
    parts.add('+ ${m.optionName(o['id'])}$side');
  }
  if ((it['notes'] ?? '').toString().isNotEmpty) parts.add('Note: ${it['notes']}');
  return parts.join(' · ');
}
