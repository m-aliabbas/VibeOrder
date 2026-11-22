import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> fixture(String name) =>
    jsonDecode(File('test/fixtures/$name').readAsStringSync()) as Map<String, dynamic>;

/// In-memory branch server for widget tests. Records requests; can drop a response once.
class FakeBackend {
  final requests = <http.Request>[];
  final roundKeys = <String>[];
  bool dropNextRoundResponse = false;
  int roundsCommitted = 0;
  final amendments = <Map<String, dynamic>>[];
  final refunds = <Map<String, dynamic>>[];
  final tenders = <Map<String, dynamic>>[];
  final discounts = <Map<String, dynamic>>[];
  final handovers = <Map<String, dynamic>>[];
  final opened = <Map<String, dynamic>>[];
  final reprints = <Map<String, dynamic>>[];
  final availability = <Map<String, dynamic>>[];
  final recipesPublished = <Map<String, dynamic>>[];
  final purchaseOrders = <Map<String, dynamic>>[];
  final countCalls = <Map<String, dynamic>>[];
  Map<String, dynamic>? countSheet;
  final cheeseItem = {
    'id': 'I1',
    'code': 'cheese',
    'name': 'Mozzarella',
    'unit': 'g',
    'purchase_unit': '5 kg bag',
    'purchase_factor': '5000.000',
    'on_hand': '4000.000',
    'reorder_level': '5000.000',
    'par_level': '15000.000',
    'status': 'low',
    'avg_cost_minor': '240',
    'cost_missing': false,
    'revision': 1,
    'active': true,
  };
  Map<String, dynamic> po(String status) => {
    'id': 'PO1',
    'number': 'PO-B1-0001',
    'status': status,
    'revision': 1,
    'supplier': {'id': 'S1', 'name': 'Dairy Co', 'phone': '0300 1234567'},
    'expected_on': null,
    'note': '',
    'created_by': 'Zara (Manager)',
    'created_at': '2026-11-18T10:00:00Z',
    'sent_at': null,
    'closed_reason': '',
    'expected_total_minor': 0,
    'receipts': <dynamic>[],
    'message': 'Demo Pizzeria\nPurchase order PO-B1-0001\n\n- Mozzarella: 3 x 5 kg bag',
    'lines': [
      {
        'id': 1,
        'item': 'cheese',
        'name': 'Mozzarella',
        'unit': 'g',
        'purchase_unit': '5 kg bag',
        'purchase_factor': '5000.000',
        'quantity': '15000.000',
        'received': '0.000',
        'outstanding': '15000.000',
        'expected_cost_minor': 0,
      },
    ],
  };
  List<Map<String, dynamic>> alerts = [
    {
      'id': 'stock:out:olives',
      'category': 'stock',
      'severity': 'critical',
      'title': 'Black olives is out of stock',
      'detail': 'On hand 0 g. Still on sale: Olives',
      'at': '2026-11-18T10:00:00Z',
      'action': {
        'type': 'sellout',
        'targets': [
          {'kind': 'option', 'code': 'olives', 'name': 'Olives'},
        ],
      },
    },
    {
      'id': 'print:J1',
      'category': 'print',
      'severity': 'critical',
      'title': 'KOT 17 #D001 may not have printed on Pizza kitchen printer',
      'detail': 'Check the printer for this ticket. If missing, reprint a COPY.',
      'at': '2026-11-18T10:00:00Z',
      'action': {'type': 'reprint', 'job': 'J1'},
    },
  ];
  final fries = {
    'id': 'R1',
    'target_kind': 'product',
    'target_code': 'fries',
    'size_code': 'regular',
    'label': 'Fries',
    'version': 1,
    'active': true,
    'note': '',
    'created_by': null,
    'created_at': '2026-11-18T10:00:00Z',
    'cost_minor': 10100,
    'cost_missing': false,
    'components': [
      {'item': 'potato', 'name': 'Frozen fries', 'unit': 'g', 'quantity': '200.000', 'cost_minor': 9000},
      {'item': 'oil', 'name': 'Cooking oil', 'unit': 'ml', 'quantity': '20.000', 'cost_minor': 1100},
    ],
  };
  final kot = {
    'id': 'J1',
    'kind': 'kitchen',
    'status': 'handed_to_spooler',
    'printer': 'Pizza kitchen printer',
    'ticket_seq': 17,
    'station': 'pizza',
    'ticket_kind': 'original',
    'check_token': 'D001',
    'receipt_no': '',
    'is_copy': false,
    'reason': '',
    'created_at': '2026-11-18T10:00:00Z',
    'next_action': '',
  };
  List<Map<String, dynamic>> packing = [];
  final menu = fixture('menu.json');
  final floor = fixture('floor.json');
  final bootstrap = fixture('bootstrap_waiter.json');

  Map<String, dynamic> check = {
    'id': 'C1',
    'revision': 1,
    'status': 'open',
    'service_type': 'dine_in',
    'token': 'D001',
    'table': {'id': 'T1', 'label': '1'},
    'guest_count': 2,
    'opened_by': 'Bilal (Waiter)',
    'subtotal_minor': 0,
    'discount_minor': 0,
    'tax_minor': 0,
    'total_minor': 0,
    'paid_minor': 0,
    'balance_minor': 0,
    'rounds': <dynamic>[],
    'tenders': <dynamic>[],
    'currency': 'PKR',
  };

  final logins = <Map<String, dynamic>>[];
  final ledgerQueries = <Map<String, String>>[];
  final supplierPayments = <Map<String, dynamic>>[];
  final printerWrites = <Map<String, dynamic>>[];
  final printers = <Map<String, dynamic>>[];
  final staffWrites = <Map<String, dynamic>>[];
  final staffAccounts = <Map<String, dynamic>>[
    {
      'id': 's1',
      'username': 'bilal',
      'display_name': 'Bilal (Waiter)',
      'roles': ['waiter'],
      'is_active': true,
      'last_login_at': null,
      'locked': false,
    },
  ];

  late final client = MockClient((req) async {
    requests.add(req);
    final path = req.url.path.replaceFirst(RegExp(r'^/api/v1/branches/[^/]+/'), '');
    http.Response json(Object body, [int status = 200]) =>
        http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json; charset=utf-8'});
    if (path == 'bootstrap') return json(bootstrap);
    if (path == 'menu') return json(menu);
    if (path == 'floor') return json(floor);
    if (path == 'dashboard') {
      return json({
        'branch': {'id': 'B1', 'name': 'Demo Pizzeria', 'currency': 'PKR'},
        'range': {'from': '2026-11-16', 'to': '2026-11-22', 'days': int.parse(req.url.queryParameters['days'] ?? '7')},
        'kpis': {
          'net_minor': 12345600,
          'net_change_pct': 12.5,
          'orders': 321,
          'orders_change_pct': -3.0,
          'avg_minor': 384600,
          'avg_change_pct': 8.0,
          'discounts_minor': 0,
          'refunds_minor': 0,
          'open_checks': 4,
          'open_value_minor': 560000,
          'in_kitchen': 7,
          'late_tickets': 2,
          'kitchen_median_min': 14.2,
          'kitchen_p90_min': 23.0,
        },
        'sales_by_day': [
          for (var i = 0; i < 7; i++)
            {'date': '2026-11-${16 + i}', 'net_minor': 1500000 + i * 100000, 'orders': 40 + i},
        ],
        'by_hour': [
          for (var h = 0; h < 24; h++) {'hour': h, 'orders': h >= 12 && h <= 22 ? h : 0, 'net_minor': 0},
        ],
        'channels': [
          {'code': 'dine_in', 'name': 'Dine-in', 'orders': 200, 'net_minor': 8000000},
          {'code': 'delivery', 'name': 'Delivery', 'orders': 121, 'net_minor': 4345600},
        ],
        'payments': [
          {'method': 'cash', 'name': 'Cash', 'amount_minor': 9000000, 'count': 250},
          {'method': 'external_card', 'name': 'Card', 'amount_minor': 3345600, 'count': 71},
        ],
        'top_items': [
          {'name': 'Chicken Tikka', 'quantity': 120, 'net_minor': 4000000},
          {'name': 'Fries', 'quantity': 90, 'net_minor': 900000},
        ],
        'staff': [
          {'name': 'Bilal (Waiter)', 'orders': 150, 'net_minor': 6000000},
        ],
      });
    }
    if (path == 'staff' && req.method == 'GET') {
      return json({
        'staff': staffAccounts,
        'roles': [
          {'code': 'waiter', 'name': 'Waiter'},
        ],
      });
    }
    if (path == 'staff' && req.method == 'POST') {
      final body = Map<String, dynamic>.from(jsonDecode(req.body));
      staffWrites.add(body);
      if ((body['password'] as String).length < 6) {
        return json({
          'code': 'validation_error',
          'message': 'Password must be at least 6 characters',
          'field_errors': {'password': 'too_short'},
        }, 422);
      }
      final row = {
        'id': 'n${staffAccounts.length}',
        'username': body['username'],
        'display_name': body['display_name'],
        'roles': body['roles'],
        'is_active': true,
        'last_login_at': null,
        'locked': false,
      };
      staffAccounts.add(row);
      return json(row, 201);
    }
    if (req.url.path == '/api/v1/login') {
      final body = jsonDecode(req.body);
      logins.add(Map<String, dynamic>.from(body));
      if (body['username'] != 'bilal' || body['password'] != 'bilal123') {
        return json({'code': 'invalid_credentials', 'message': 'Wrong username or password'}, 401);
      }
      return json({
        'session_token': 'sess',
        'staff_id': 's1',
        'display_name': 'Bilal (Waiter)',
        'branch_id': 'B1',
        'branch_name': 'Demo Pizzeria',
        'device_key': 'devkey',
        'roles': ['waiter'],
      }, 201);
    }
    if (path == 'quotes') {
      final items = (jsonDecode(req.body)['items'] as List);
      return json({
        'quote_id': 'Q1',
        'menu_release': 'M1',
        'total_minor': 190000 * items.length,
        'lines': [
          for (final i in items)
            {'name': 'Large Half & Half Pizza', 'total_minor': 190000, 'client_line_id': i['client_line_id']},
        ],
      }, 201);
    }
    if (path == 'checks/C1') return json(check);
    if (path == 'checks' && req.method == 'GET') {
      final status = req.url.queryParameters['status'] ?? 'open';
      return json({
        'checks': [if (check['status'] == status) check],
      });
    }
    if (path == 'checks/C1/tenders') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      tenders.add(body);
      final amt = body['amount_minor'] as int;
      check = {...check, 'revision': check['revision'] + 1, 'balance_minor': check['balance_minor'] - amt};
      return json({
        'tender_id': 'T9',
        'applied_minor': amt,
        'change_minor': (body['cash_received_minor'] ?? amt) - amt,
        'balance_minor': check['balance_minor'],
        'revision': check['revision'],
      }, 201);
    }
    if (path == 'tenders/T9/receipt') return json({'id': 'J1', 'kind': 'payer_receipt'}, 201);
    if (path == 'checks/C1/discounts' && req.method == 'GET') {
      return json({
        'limit_bp': 1000,
        'promotions': [
          {'code': 'WEEKEND10', 'name': 'Weekend 10% off', 'kind': 'percent', 'value': 1000, 'min_subtotal_minor': 0},
        ],
      });
    }
    if (path == 'checks/C1/discounts') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      discounts.add(body);
      return json({
        'total_minor': 181800,
        'discount': {'id': 'D1'},
      }, 201);
    }
    if (path == 'packing') return json({'orders': packing});
    if (path == 'deliveries') return json({'deliveries': <dynamic>[]});
    if (path == 'riders') return json({'riders': <dynamic>[]});
    if (path == 'checks/P1/handover') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      handovers.add(body);
      if (body['approval'] == null) {
        return json({'code': 'approval_required', 'message': 'Manager approval required'}, 403);
      }
      packing = [];
      return json({'id': 'P1', 'handed_over_at': '2026-11-18T10:00:00Z'});
    }
    if (path == 'zones') {
      return json({
        'zones': [
          {'id': 'Z1', 'name': 'Gulberg', 'fee_minor': 12000, 'eta_minutes': 35},
        ],
      });
    }
    if (path == 'customers') {
      return json({
        'customers': [
          {
            'id': 'CU1',
            'name': 'Hina',
            'phone_masked': '0300••••321',
            'orders': 3,
            'addresses': [
              {
                'id': 'A1',
                'text': 'House 12, Gulberg',
                'landmark': '',
                'zone': {'id': 'Z1', 'name': 'Gulberg'},
              },
            ],
          },
        ],
      });
    }
    if (path == 'customers/CU1/repeat') {
      return json({
        'check': {'token': 'R004', 'business_date': '2026-11-17'},
        'changes': 2,
        'items': [
          {
            'name': 'Large Half & Half Pizza',
            'quantity': 1,
            'status': 'price_changed',
            'old_unit_minor': 180000,
            'new_unit_minor': 190000,
            'spec': {
              'product': 'mixed-pizza',
              'variant': 'large',
              'halves': {'left': 'tikka', 'right': 'fajita'},
            },
          },
          {
            'name': 'Fries',
            'quantity': 2,
            'status': 'unavailable',
            'message': 'Fries is sold out',
            'old_unit_minor': 35000,
            'new_unit_minor': null,
            'spec': {'product': 'fries', 'variant': 'regular'},
          },
        ],
      });
    }
    if (path == 'checks' && req.method == 'POST') {
      opened.add(jsonDecode(req.body) as Map<String, dynamic>);
      return json({'check_id': 'C1', 'token': 'R010', 'revision': 1}, 201);
    }
    if (path == 'tenders/T1/refunds') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      refunds.add(body);
      if (body['approval'] == null) {
        return json({'code': 'approval_required', 'message': 'Manager approval required'}, 403);
      }
      final tenders = List<Map<String, dynamic>>.from(check['tenders']);
      tenders[0] = {...tenders[0], 'refundable_minor': tenders[0]['refundable_minor'] - body['amount_minor']};
      final r = {
        'id': 'RF1',
        'status': 'succeeded',
        'method': 'cash',
        'amount_minor': body['amount_minor'],
        'reason': body['reason'],
        'external_ref': '',
      };
      check = {
        ...check,
        'revision': check['revision'] + 1,
        'tenders': tenders,
        'refunds': [r],
      };
      return json(r, 201);
    }
    if (path == 'checks/C1/rounds') {
      final key = req.headers['Idempotency-Key']!;
      final replay = roundKeys.contains(key);
      if (!replay) {
        roundKeys.add(key);
        roundsCommitted++;
        check = {...check, 'revision': check['revision'] + 1, 'total_minor': 190000 * roundsCommitted};
      }
      if (dropNextRoundResponse) {
        dropNextRoundResponse = false;
        throw const SocketException('connection reset after commit');
      }
      return http.Response(
        jsonEncode({'check_id': 'C1', 'round': roundsCommitted, 'revision': check['revision']}),
        201,
        headers: {'content-type': 'application/json', if (replay) 'idempotent-replay': 'true'},
      );
    }
    if (path == 'print-jobs') {
      return json({
        'jobs': [kot],
      });
    }
    if (path == 'print-jobs/J1') {
      return json({
        ...kot,
        'content': 'PIZZA OVEN  TICKET 17\n1 x LARGE MIXED PIZZA\nRIGHT half: Fajita',
        'copies': [],
      });
    }
    if (path == 'print-jobs/J1/reprints') {
      reprints.add(jsonDecode(req.body) as Map<String, dynamic>);
      return json({...kot, 'id': 'J2', 'is_copy': true}, 201);
    }
    if (path == 'checks/C1/amendments') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      amendments.add(body);
      if (body['action'] == 'split') {
        check = {...check, 'revision': check['revision'] + 1};
        return json({'check_id': 'C1', 'revision': check['revision'], 'description': 'split 2 into 1 + 1'});
      }
      if (body['expected_revision'] != check['revision']) {
        return json({
          'code': 'stale_revision',
          'message': 'The check changed.',
          'current_revision': check['revision'],
        }, 409);
      }
      if (body['approval'] == null) {
        return json({'code': 'approval_required', 'message': 'Manager approval required'}, 403);
      }
      final rounds = List<Map<String, dynamic>>.from(check['rounds']);
      for (final r in rounds) {
        r['lines'] = [
          for (final l in List<Map<String, dynamic>>.from(r['lines']))
            l['id'] == body['line_id'] ? {...l, 'status': 'voided'} : l,
        ];
      }
      check = {
        ...check,
        'revision': check['revision'] + 1,
        'total_minor': 0,
        'rounds': rounds,
        'adjustments': [
          {
            'id': 'A1',
            'kind': 'remove',
            'description': 'Table 1, pizza 1: removed 1 x Large Half & Half Pizza',
            'reason': body['reason'],
            'amount_delta_minor': -190000,
            'staff': 'Bilal (Waiter)',
            'approved_by': 'Zara (Manager)',
          },
        ],
      };
      return json({'check_id': 'C1', 'revision': check['revision'], 'description': 'Table 1, pizza 1: removed'});
    }
    if (path == 'alerts') {
      final counts = {'critical': 0, 'warning': 0, 'info': 0};
      for (final a in alerts) {
        counts[a['severity']] = counts[a['severity']]! + 1;
      }
      return json({'alerts': alerts, 'counts': counts});
    }
    if (path == 'availability' && req.method == 'GET') return json({'sold_out': availability});
    if (path == 'availability') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      availability.add({...body, 'actor': 'Zara (Manager)', 'since': '2026-11-18T10:05:00Z'});
      alerts = [
        for (final a in alerts)
          if (a['id'] != 'stock:out:olives') a,
      ];
      return json({'target_kind': body['target_kind'], 'target_code': body['target_code'], 'available': false});
    }
    if (path == 'stock') {
      return json({
        'items': [cheeseItem],
      });
    }
    if (path.startsWith('ledgers/')) {
      ledgerQueries.add({'kind': path.substring(8), ...req.url.queryParameters});
      final supplier = req.url.queryParameters['supplier'];
      return json({
        'kind': path.substring(8),
        'title': 'Supplier ledger',
        'from': req.url.queryParameters['from'],
        'to': req.url.queryParameters['to'],
        'summary': [
          {'label': 'Total owed', 'value': 1000000, 'money': true},
        ],
        'sections': [
          {
            'title': supplier == null ? 'All suppliers' : 'Dairy Co',
            'subtitle': '',
            'columns': [
              {'key': 'type', 'label': 'Entry', 'money': false, 'align': 'left'},
              {'key': 'balance', 'label': 'Balance owed', 'money': true, 'align': 'right'},
            ],
            'rows': [
              {'id': 'S1', 'type': 'Goods received', 'supplier': 'Dairy Co', 'balance': 2600000},
              {'type': 'Payment (Bank transfer)', 'balance': 1000000},
            ],
            'totals': {'type': 'Closing balance', 'balance': 1000000},
          },
        ],
        'print_path': '/print/ledger/TOKEN',
      });
    }
    if (path.startsWith('stock/suppliers/') && path.endsWith('/payments')) {
      supplierPayments.add(Map<String, dynamic>.from(jsonDecode(req.body)));
      return json({'id': 'P1', 'balance_minor': 1000000}, 201);
    }
    if (path == 'printers' && req.method == 'GET') {
      return json({
        'printers': printers,
        'installed': ['EPSON TM-T88V', 'Microsoft Print to PDF'],
        'stations': [
          {'id': 'ST1', 'name': 'Pizza oven'},
        ],
        'transports': [],
        'receipt': {
          'business_name': 'Demo Pizzeria',
          'branch_name': 'Gulberg',
          'address': '',
          'phone': '',
          'tax_number': '',
          'receipt_footer': '',
        },
      });
    }
    if (path == 'printers' && req.method == 'POST') {
      final body = Map<String, dynamic>.from(jsonDecode(req.body));
      printerWrites.add(body);
      final row = {...body, 'id': 'PR${printers.length}', 'station': null, 'last_error': '', 'last_ok_at': null};
      printers.add(row);
      return json(row, 201);
    }
    if (path == 'stock/suppliers') {
      return json({
        'suppliers': [
          {'id': 'S1', 'name': 'Dairy Co', 'phone': '0300 1234567', 'note': '', 'balance_minor': 2600000},
        ],
      });
    }
    if (path == 'stock/suggestions') {
      return json({
        'suggestions': [
          {
            ...cheeseItem,
            'item': 'cheese',
            'on_order': '0',
            'suggested_packs': 3,
            'suggested_quantity': '15000.000',
            'estimated_cost_minor': 360000,
          },
        ],
      });
    }
    if (path == 'stock/purchase-orders' && req.method == 'GET') return json({'orders': purchaseOrders});
    if (path == 'stock/purchase-orders') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      purchaseOrders.add({...po(body['send'] == true ? 'sent' : 'draft'), 'request': body});
      return json(purchaseOrders.last, 201);
    }
    if (path == 'stock/purchase-orders/PO1') return json(purchaseOrders.last);
    if (path == 'stock/count-sheets' && req.method == 'GET') {
      return json({
        'counts': [?countSheet],
      });
    }
    if (path == 'stock/count-sheets') {
      countSheet = {
        'id': 'CS1',
        'number': 'CT-B1-0001',
        'status': 'open',
        'revision': 1,
        'created_by': 'Zara (Manager)',
        'created_at': '2026-11-18T10:00:00Z',
        'counted': 0,
        'total': 1,
        'variance_value_minor': 0,
        'lines': [
          {'item': 'cheese', 'name': 'Mozzarella', 'unit': 'g', 'counted': null, 'expected': null, 'variance': null},
        ],
      };
      return json(countSheet!, 201);
    }
    if (path == 'stock/count-sheets/CS1' && req.method == 'GET') return json(countSheet!);
    if (path == 'stock/count-sheets/CS1/lines' || path == 'stock/count-sheets/CS1') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      countCalls.add({'path': path, ...body});
      if (body['action'] == 'approve') {
        countSheet = {...countSheet!, 'status': 'approved', 'variance_value_minor': -120000};
        return json(countSheet!);
      }
      countSheet = {
        ...countSheet!,
        'revision': 2,
        'counted': 1,
        'variance_value_minor': -120000,
        'lines': [
          {
            'item': 'cheese',
            'name': 'Mozzarella',
            'unit': 'g',
            'counted': '3500.000',
            'expected': '4000.000',
            'variance': '-500.000',
            'variance_value_minor': -120000,
            'counted_by': 'Zara (Manager)',
          },
        ],
      };
      return json(countSheet!);
    }
    if (path == 'stock/recipes' && req.method == 'GET') {
      return json({
        'recipes': [fries],
        'missing': [
          {'target_kind': 'product', 'target_code': 'wings', 'size_code': '', 'label': 'Wings'},
        ],
        'targets': [
          {
            'target_kind': 'product',
            'target_code': 'fries',
            'label': 'Fries',
            'required': true,
            'sizes': [
              {'code': 'regular', 'name': 'Regular'},
            ],
          },
        ],
        'items': [
          {'code': 'potato', 'name': 'Frozen fries', 'unit': 'g'},
          {'code': 'oil', 'name': 'Cooking oil', 'unit': 'ml'},
        ],
      });
    }
    if (path == 'stock/recipes') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      recipesPublished.add(body);
      return json({
        'retired': false,
        'recipe': {...fries, 'version': 2},
      }, 201);
    }
    return json({'code': 'not_found', 'message': 'no route $path'}, 404);
  });
}
