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

  late final client = MockClient((req) async {
    requests.add(req);
    final path = req.url.path.replaceFirst(RegExp(r'^/api/v1/branches/[^/]+/'), '');
    http.Response json(Object body, [int status = 200]) =>
        http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json; charset=utf-8'});
    if (path == 'bootstrap') return json(bootstrap);
    if (path == 'menu') return json(menu);
    if (path == 'floor') return json(floor);
    if (path == 'staff-directory') {
      return json({
        'staff': [
          {
            'id': 's1',
            'username': 'bilal',
            'display_name': 'Bilal (Waiter)',
            'roles': ['waiter'],
          },
        ],
      });
    }
    if (path == 'sessions') {
      final body = jsonDecode(req.body);
      if (body['pin'] != '1111') return json({'code': 'invalid_credentials', 'message': 'Unknown staff or PIN'}, 401);
      return json({'session_token': 'sess', 'staff_id': 's1'}, 201);
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
    return json({'code': 'not_found', 'message': 'no route $path'}, 404);
  });
}
