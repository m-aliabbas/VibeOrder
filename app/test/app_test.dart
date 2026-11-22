import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:vibeorder_staff/api/api_client.dart';
import 'package:vibeorder_staff/api/menu.dart';
import 'package:vibeorder_staff/main.dart';
import 'package:vibeorder_staff/screens/alerts_screen.dart';
import 'package:vibeorder_staff/screens/cashier_screen.dart';
import 'package:vibeorder_staff/screens/dashboard_screen.dart';
import 'package:vibeorder_staff/screens/dispatch_screen.dart';
import 'package:vibeorder_staff/screens/ledgers_screen.dart';
import 'package:vibeorder_staff/screens/order_screen.dart';
import 'package:vibeorder_staff/screens/pizza_builder.dart';
import 'package:vibeorder_staff/screens/printers_screen.dart';
import 'package:vibeorder_staff/screens/staff_screen.dart';
import 'package:vibeorder_staff/screens/stock_purchasing.dart';
import 'package:vibeorder_staff/screens/stock_screen.dart';
import 'package:vibeorder_staff/state/app_state.dart';
import 'package:vibeorder_staff/widgets/common.dart';
import 'package:vibeorder_staff/widgets/print_log.dart';

import 'fake_backend.dart';

Future<(AppState, FakeBackend)> signedIn({String staffId = 's1'}) async {
  SharedPreferences.setMockInitialValues({'server': 'http://fake'});
  final prefs = await SharedPreferences.getInstance();
  final fake = FakeBackend();
  final api = ApiClient(baseUrl: 'http://fake', client: fake.client);
  final app = AppState(prefs, api: api);
  await app.login('http://fake', 'bilal', 'bilal123');
  app.bootstrap!['staff']['id'] = staffId;
  return (app, fake);
}

Widget host(AppState app, Widget child) => ChangeNotifierProvider.value(
  value: app,
  child: MaterialApp(theme: buildTheme(Brightness.light), home: child),
);

void main() {
  test('money formats PKR minor units', () {
    expect(money(190000), 'Rs 1,900');
    expect(money(12345), 'Rs 123.45');
    expect(parseRupees('1,900'), 190000);
    expect(parseRupees('abc'), isNull);
  });

  testWidgets('username + password sign in lands on the floor and remembers the device', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    SharedPreferences.setMockInitialValues({'server': 'http://fake'});
    final prefs = await SharedPreferences.getInstance();
    final fake = FakeBackend();
    final app = AppState(
      prefs,
      api: ApiClient(baseUrl: 'http://fake', client: fake.client),
    );
    await tester.pumpWidget(VibeOrderApp(state: app, splash: Duration.zero));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('pair-code')), findsNothing, reason: 'no pairing step');
    await tester.enterText(find.byKey(const ValueKey('username')), 'bilal');
    await tester.enterText(find.byKey(const ValueKey('password')), 'wrong');
    await tester.tap(find.byKey(const ValueKey('sign-in')));
    await tester.pumpAndSettle();
    expect(find.text('Wrong username or password'), findsOneWidget);
    expect(app.stage, Stage.login);

    await tester.enterText(find.byKey(const ValueKey('password')), 'bilal123');
    await tester.tap(find.byKey(const ValueKey('sign-in')));
    await tester.pumpAndSettle();
    expect(app.stage, Stage.ready);
    expect(find.text('Floor'), findsWidgets);
    expect(find.byKey(const ValueKey('table-1')), findsOneWidget);
    expect(prefs.getString('device_key'), 'devkey');
    expect(prefs.getString('session_token'), 'sess', reason: 'reopening the app stays signed in');

    // Sign out and back in: the same device key is sent, so the server reuses the device record.
    await app.logout();
    await tester.pumpAndSettle();
    expect(app.stage, Stage.login);
    await tester.enterText(find.byKey(const ValueKey('password')), 'bilal123');
    await tester.tap(find.byKey(const ValueKey('sign-in')));
    await tester.pumpAndSettle();
    expect(fake.logins.last['device_key'], 'devkey');
    expect(fake.logins.last['username'], 'bilal', reason: 'last username is prefilled');
  });

  testWidgets('pizza builder labels halves in words and builds the spec item', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, _) = await signedIn();
    Map<String, dynamic>? result;
    await tester.pumpWidget(
      host(
        app,
        Builder(
          builder: (context) {
            return Scaffold(
              body: Center(
                child: FilledButton(
                  onPressed: () async => result = await PizzaBuilder.open(
                    context,
                    MenuView(app.menu!),
                    initial: {'product': 'mixed-pizza'},
                  ),
                  child: const Text('open'),
                ),
              ),
            );
          },
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('LEFT HALF'), findsOneWidget);
    expect(find.text('RIGHT HALF'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('crust-stuffed')));
    await tester.tap(find.byKey(const ValueKey('left-tikka')));
    await tester.tap(find.byKey(const ValueKey('right-fajita')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const ValueKey('topping-extra-cheese')));
    await tester.tap(
      find.descendant(of: find.byKey(const ValueKey('topping-extra-cheese')), matching: find.text('Right')),
    );
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('builder-price')), findsOneWidget);
    expect(find.text('Rs 1,900'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('builder-confirm')));
    await tester.pumpAndSettle();
    expect(result!['product'], 'mixed-pizza');
    expect(result!['variant'], 'large');
    expect(result!['crust'], 'stuffed');
    expect(result!['halves'], {'left': 'tikka', 'right': 'fajita'});
    expect(result!['options'], [
      {'id': 'extra-cheese', 'side': 'right'},
    ]);
  });

  testWidgets('A03/A06: dropped response keeps the command; retry reuses key; draft scoped to operator', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    final item = {'client_line_id': 'x1', 'product': 'cola-500', 'variant': 'regular', 'quantity': 1, 'fire': 'now'};
    await app.saveDraft('C1', [item]);

    // Another waiter on the same device cannot see (or send) this draft.
    app.bootstrap!['staff']['id'] = 's2';
    expect(app.loadDraft('C1'), isEmpty);
    app.bootstrap!['staff']['id'] = 's1';
    expect(app.loadDraft('C1'), hasLength(1));

    fake.dropNextRoundResponse = true;
    await tester.pumpWidget(host(app, const OrderScreen(checkId: 'C1', title: 'Table 1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('send')));
    await tester.pumpAndSettle();
    expect(fake.roundsCommitted, 1, reason: 'server committed');
    expect(app.loadPending('C1'), isNotNull, reason: 'client could not confirm, keeps the command');
    expect(find.byKey(const ValueKey('retry-send')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('retry-send')));
    await tester.pumpAndSettle();
    expect(fake.roundKeys, hasLength(1), reason: 'same idempotency key reused');
    expect(fake.roundsCommitted, 1, reason: 'no duplicate kitchen job');
    expect(app.loadPending('C1'), isNull);
    expect(app.loadDraft('C1'), isEmpty);
    expect(find.textContaining('Already sent earlier'), findsOneWidget);
    await tester.pump(const Duration(seconds: 7)); // let the polling timer tick once
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('removing a cooked sent line asks for a manager, then resends with approval', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    fake.check = {
      ...fake.check,
      'revision': 2,
      'total_minor': 190000,
      'rounds': [
        {
          'id': 'R1',
          'number': 1,
          'added_by': 'Bilal (Waiter)',
          'lines': [
            {
              'id': 'L1',
              'line_no': 1,
              'name': 'Large Half & Half Pizza',
              'quantity': 1,
              'total_minor': 190000,
              'status': 'active',
              'fire_state': 'fired',
              'production_status': ['ready'],
              'spec': {
                'product': 'mixed-pizza',
                'variant': 'large',
                'halves': {'left': 'tikka', 'right': 'fajita'},
                'options': [],
              },
            },
          ],
        },
      ],
    };
    await tester.pumpWidget(host(app, const OrderScreen(checkId: 'C1', title: 'Table 1')));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('line-menu-1')));
    await tester.pumpAndSettle();
    expect(find.text('Change'), findsNothing, reason: 'cooked food cannot be changed, only removed or remade');
    expect(find.text('Remake'), findsOneWidget);
    await tester.tap(find.text('Remove'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Wrong entry'));
    await tester.pumpAndSettle();
    expect(find.text('Manager approval'), findsOneWidget);
    expect(fake.amendments.single['approval'], isNull);

    await tester.enterText(find.byKey(const ValueKey('approver')), 'zara');
    await tester.enterText(find.byKey(const ValueKey('approver-password')), '9999');
    await tester.tap(find.byKey(const ValueKey('approve')));
    await tester.pumpAndSettle();
    expect(fake.amendments, hasLength(2));
    expect(fake.amendments.last['approval'], {'username': 'zara', 'password': '9999'});
    expect(fake.amendments.last['action'], 'remove');
    expect(fake.amendments.last['reason'], 'Wrong entry');
    expect(find.text('removed'), findsOneWidget, reason: 'voided line stays visible, struck through');
    expect(find.text('CORRECTIONS'), findsOneWidget);
    await tester.pump(const Duration(seconds: 7));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('cashier refunds part of a closed cash bill with manager approval', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    (app.bootstrap!['permissions'] as List).add('tender.take');
    fake.check = {
      ...fake.check,
      'status': 'closed',
      'receipt_no': 'B1-000009',
      'revision': 5,
      'total_minor': 190000,
      'paid_minor': 190000,
      'tenders': [
        {
          'id': 'T1',
          'method': 'cash',
          'status': 'succeeded',
          'amount_minor': 190000,
          'change_minor': 10000,
          'external_ref': '',
          'refundable_minor': 190000,
        },
      ],
      'refunds': <dynamic>[],
    };
    await tester.pumpWidget(host(app, const Scaffold(body: CashierScreen())));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Closed'));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('#D001'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('close-check')), findsOneWidget);
    expect(tester.widget<FilledButton>(find.byKey(const ValueKey('close-check'))).onPressed, isNull);

    await tester.tap(find.byKey(const ValueKey('refund-T1')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('refund-amount')), '300');
    await tester.tap(find.text('Cold food'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('refund-ok')));
    await tester.pumpAndSettle();
    expect(find.text('Manager approval'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('approver')), 'zara');
    await tester.enterText(find.byKey(const ValueKey('approver-password')), '9999');
    await tester.tap(find.byKey(const ValueKey('approve')));
    await tester.pumpAndSettle();

    expect(fake.refunds, hasLength(2));
    expect(fake.refunds.last, containsPair('amount_minor', 30000));
    expect(fake.refunds.last['reason'], 'Cold food');
    expect(fake.refunds.last['expected_revision'], 5);
    expect(find.textContaining('Refund cash · Cold food'), findsOneWidget);
    await tester.pump(const Duration(seconds: 6));
    await tester.pumpWidget(const SizedBox());
  });

  Map<String, dynamic> openBill() => {
    'status': 'open',
    'revision': 3,
    'subtotal_minor': 202000,
    'total_minor': 202000,
    'balance_minor': 202000,
    'tenders': <dynamic>[],
    'refunds': <dynamic>[],
    'discounts': <dynamic>[],
    'rounds': [
      {
        'number': 1,
        'lines': [
          {
            'id': 'L1',
            'line_no': 1,
            'status': 'active',
            'quantity': 1,
            'name': 'Large Half & Half Pizza',
            'product': 'mixed-pizza',
            'total_minor': 190000,
            'charge_minor': 190000,
            'fire_state': 'fired',
            'production_status': ['queued'],
            'spec': {'product': 'mixed-pizza', 'variant': 'large'},
          },
          {
            'id': 'L2',
            'line_no': 2,
            'status': 'active',
            'quantity': 1,
            'name': 'Cola 500ml',
            'product': 'cola-500',
            'total_minor': 12000,
            'charge_minor': 12000,
            'fire_state': 'fired',
            'spec': {'product': 'cola-500'},
            'production_status': ['queued'],
          },
        ],
      },
    ],
  };

  Future<(AppState, FakeBackend)> cashierOn(WidgetTester tester, Widget screen, {List<String> perms = const []}) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    (app.bootstrap!['permissions'] as List).addAll(['tender.take', 'discount.apply', ...perms]);
    fake.check = {...fake.check, ...openBill()};
    await tester.pumpWidget(host(app, Scaffold(body: screen)));
    await tester.pumpAndSettle();
    return (app, fake);
  }

  testWidgets('split by item pays only the ticked line and offers a payer receipt', (tester) async {
    final (_, fake) = await cashierOn(tester, const CashierScreen());
    await tester.tap(find.textContaining('#D001'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('split-items')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('split-1')));
    await tester.pumpAndSettle();
    expect(find.text('This payer: Rs 1,900'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('split-cash')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('tender-received')), '2000');
    await tester.tap(find.byKey(const ValueKey('tender-confirm')));
    await tester.pumpAndSettle();
    expect(fake.tenders.single['amount_minor'], 190000);
    expect(fake.tenders.single['allocations'], [
      {'line_id': 'L1', 'amount_minor': 190000},
    ]);
    expect(find.textContaining('Give change Rs 100'), findsOneWidget);
    await tester.tap(find.text('Print payer receipt'));
    await tester.pumpAndSettle();
    expect(fake.requests.any((r) => r.url.path.endsWith('tenders/T9/receipt')), isTrue);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('cashier applies an order promotion with the observed revision', (tester) async {
    final (_, fake) = await cashierOn(tester, const CashierScreen());
    await tester.tap(find.textContaining('#D001'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('discount')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('promo-WEEKEND10')));
    await tester.tap(find.byKey(const ValueKey('discount-apply')));
    await tester.pumpAndSettle();
    expect(fake.discounts.single, containsPair('promotion_code', 'WEEKEND10'));
    expect(fake.discounts.single['expected_revision'], 3);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('A13: incomplete pack needs a reason and manager approval', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    fake.packing = [
      {
        'id': 'P1',
        'token': 'P007',
        'revision': 4,
        'service_type': 'pickup',
        'channel': 'staff',
        'customer_name': 'Sara',
        'promised_at': null,
        'late': false,
        'complete': false,
        'balance_minor': 0,
        'notes': '',
        'components': [
          {
            'id': 'I1',
            'label': 'LARGE MIXED PIZZA',
            'quantity': 1,
            'station': 'pizza',
            'status': 'ready',
            'done': true,
            'revision': 3,
          },
          {
            'id': 'I2',
            'label': 'COLA 500ML',
            'quantity': 1,
            'station': 'packing',
            'status': 'queued',
            'done': false,
            'revision': 1,
          },
        ],
      },
    ];
    await tester.pumpWidget(host(app, const Scaffold(body: DispatchScreen())));
    await tester.pumpAndSettle();
    expect(find.text('Hand over anyway…'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('handover-P007')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Guest will collect later'));
    await tester.pumpAndSettle();
    expect(find.text('Manager approval'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('approver')), 'zara');
    await tester.enterText(find.byKey(const ValueKey('approver-password')), '9999');
    await tester.tap(find.byKey(const ValueKey('approve')));
    await tester.pumpAndSettle();
    expect(fake.handovers, hasLength(2));
    expect(fake.handovers.last['override_reason'], 'Guest will collect later');
    expect(fake.handovers.last['expected_revision'], 4);
    expect(find.text('Nothing to pack'), findsOneWidget);
    await tester.pump(const Duration(seconds: 6));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('A10: phone repeat shows price changes, drops sold-out items, confirms the address', (tester) async {
    final (app, fake) = await cashierOn(tester, const CashierScreen(), perms: ['customer.lookup']);
    await tester.tap(find.byKey(const ValueKey('new-delivery')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('phone-number')), '03007654321');
    await tester.tap(find.byKey(const ValueKey('phone-lookup')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('customer-CU1')));
    await tester.pumpAndSettle();
    expect(find.textContaining('was Rs 1,800 → Rs 1,900'), findsOneWidget);
    expect(find.text('Fries is sold out'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('repeat-order')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Read the address back'), findsOneWidget); // must confirm even on repeat
    expect(fake.opened, isEmpty);
    await tester.ensureVisible(find.byKey(const ValueKey('address-confirmed')));
    await tester.tap(find.byKey(const ValueKey('address-confirmed')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('repeat-order')));
    await tester.pumpAndSettle();
    expect(fake.opened.single['service_type'], 'delivery');
    expect(fake.opened.single['zone_id'], 'Z1');
    expect(fake.opened.single['address_text'], 'House 12, Gulberg');
    final draft = app.loadDraft('C1');
    expect(draft.map((d) => d['product']), ['mixed-pizza']);
    await tester.pump(const Duration(seconds: 7));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('KOT log: preview the printed ticket and reprint a labelled COPY with a reason', (tester) async {
    final (_, fake) = await cashierOn(tester, const PrintLogScreen(), perms: ['print.reprint']);
    expect(find.text('KOT #17 pizza'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('job-J1')));
    await tester.pumpAndSettle();
    expect(find.textContaining('RIGHT half: Fajita'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('reprint-copy')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Kitchen lost ticket'));
    await tester.pumpAndSettle();
    expect(fake.reprints.single['reason'], 'Kitchen lost ticket');
    await tester.pump(const Duration(seconds: 9));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('quantities: draft stepper, and split a sent quantity-2 line', (tester) async {
    final (app, fake) = await cashierOn(
      tester,
      const OrderScreen(checkId: 'C1', title: 'Table 1'),
      perms: ['check.amend'],
    );
    await app.saveDraft('C1', [
      {'client_line_id': 'x1', 'product': 'cola-500', 'variant': 'regular', 'quantity': 1, 'fire': 'now'},
    ]);
    await tester.pumpWidget(host(app, const Scaffold(body: SizedBox())));
    await tester.pumpWidget(host(app, const OrderScreen(checkId: 'C1', title: 'Table 1')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('draft-plus-0')));
    await tester.pumpAndSettle();
    expect(app.loadDraft('C1').single['quantity'], 2);
    await tester.tap(find.byKey(const ValueKey('draft-minus-0')));
    await tester.pumpAndSettle();
    expect(app.loadDraft('C1').single['quantity'], 1);
    // Sent line with quantity 2 → Split…
    fake.check = {
      ...fake.check,
      'rounds': [
        {
          'number': 1,
          'added_by': 'Bilal (Waiter)',
          'lines': [
            {
              'id': 'L1',
              'line_no': 1,
              'status': 'active',
              'quantity': 2,
              'name': 'Large Half & Half Pizza',
              'product': 'mixed-pizza',
              'total_minor': 380000,
              'charge_minor': 380000,
              'fire_state': 'fired',
              'production_status': ['preparing'],
              'spec': {'product': 'mixed-pizza', 'variant': 'large'},
            },
          ],
        },
      ],
    };
    await tester.pump(const Duration(seconds: 7));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('line-menu-1')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Split…'));
    await tester.pumpAndSettle();
    expect(find.text('Result: 1 × and 1 × (kitchen progress is kept)'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('split-confirm')));
    await tester.pumpAndSettle();
    expect(fake.amendments.single, containsPair('action', 'split'));
    expect(fake.amendments.single['quantity'], 1);
    expect(fake.amendments.single['line_id'], 'L1');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('alerts: out-of-stock offers sold out; KOT print problem opens the ticket', (tester) async {
    final (app, fake) = await cashierOn(
      tester,
      const AlertsScreen(),
      perms: ['stock.view', 'availability.set', 'print.reprint'],
    );
    expect(find.text('Black olives is out of stock'), findsOneWidget);
    expect(find.textContaining('may not have printed'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('alert-action-stock:out:olives')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('sellout-olives')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('sellout-confirm')));
    await tester.pumpAndSettle();
    expect(fake.availability.single['target_code'], 'olives');
    expect(fake.availability.single['available'], false);
    expect(find.text('Black olives is out of stock'), findsNothing);
    expect(app.alertCounts['critical'], 1);
    await tester.tap(find.byKey(const ValueKey('alert-action-print:J1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('reprint-copy')), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('recipes: missing recipe listed; changing fries publishes v2 with the observed version', (tester) async {
    final (_, fake) = await cashierOn(tester, const RecipesTab(), perms: ['stock.view', 'stock.manage']);
    expect(find.text('Wings'), findsOneWidget);
    expect(find.text('Fries · regular'), findsOneWidget);
    await tester.tap(find.text('Fries · regular'));
    await tester.pumpAndSettle();
    expect(find.text('Change recipe · v1 → v2'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('recipe-qty-0')), '250');
    await tester.enterText(find.byKey(const ValueKey('recipe-note')), 'bigger portion');
    await tester.tap(find.byKey(const ValueKey('recipe-publish')));
    await tester.pumpAndSettle();
    final body = fake.recipesPublished.single;
    expect(body['expected_version'], 1);
    expect(body['size_code'], 'regular');
    expect(body['components'], [
      {'item': 'potato', 'quantity': '250'},
      {'item': 'oil', 'quantity': '20'},
    ]);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('purchase order from suggestions: 3 bags of cheese, created as sent, supplier message shown', (
    tester,
  ) async {
    final (_, fake) = await cashierOn(tester, const PurchaseOrdersTab(), perms: ['stock.view', 'stock.manage']);
    expect(find.text('Mozzarella'), findsOneWidget);
    expect(find.textContaining('5 kg bag'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('po-from-suggestions')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('po-supplier')), 'Dairy Co');
    await tester.tap(find.byKey(const ValueKey('po-save-send')));
    await tester.pumpAndSettle();
    final body = fake.purchaseOrders.single['request'] as Map<String, dynamic>;
    expect(body['send'], true);
    expect(body['supplier'], 'Dairy Co');
    expect(body['lines'], [
      {'item': 'cheese', 'quantity': '3', 'expected_cost_minor': 360000},
    ]);
    expect(find.byKey(const ValueKey('po-message')), findsOneWidget);
    expect(find.byKey(const ValueKey('po-receive')), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('count sheet: start, enter a count, manager sees variance and approves', (tester) async {
    final (_, fake) = await cashierOn(tester, const CountSheetsTab(), perms: ['stock.view', 'stock.adjust']);
    await tester.tap(find.byKey(const ValueKey('count-start')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('count-input-cheese')), '3500');
    await tester.tap(find.byKey(const ValueKey('count-save')));
    await tester.pumpAndSettle();
    expect(fake.countCalls.first['lines'], [
      {'item': 'cheese', 'counted': '3500'},
    ]);
    expect(find.textContaining('variance -500 g'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('count-approve')));
    await tester.pumpAndSettle();
    expect(fake.countCalls.last['action'], 'approve');
    expect(fake.countCalls.last['expected_revision'], 2);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('dashboard shows KPIs with change vs previous period and switches range', (tester) async {
    tester.view.physicalSize = const Size(1400, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    await tester.pumpWidget(host(app, const DashboardScreen()));
    await tester.pumpAndSettle();
    expect(find.text('Rs 123,456'), findsOneWidget);
    expect(find.text('321'), findsOneWidget);
    expect(find.textContaining('2 tickets late'), findsOneWidget);
    expect(find.text('Sales per day'), findsOneWidget);
    expect(find.text('Chicken Tikka'), findsOneWidget);
    await tester.tap(find.text('Today'));
    await tester.pumpAndSettle();
    expect(fake.requests.last.url.queryParameters['days'], '1');
    expect(find.text('Sales by hour'), findsOneWidget);
  });

  testWidgets('manager adds a person with username, password and roles', (tester) async {
    tester.view.physicalSize = const Size(1200, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    await tester.pumpWidget(host(app, const StaffScreen()));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('staff-bilal')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('add-staff')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('staff-name')), 'Ali Khan');
    await tester.enterText(find.byKey(const ValueKey('staff-username')), 'ali');
    await tester.enterText(find.byKey(const ValueKey('staff-password')), '123');
    await tester.tap(find.byKey(const ValueKey('role-cashier')));
    await tester.tap(find.byKey(const ValueKey('staff-save')));
    await tester.pumpAndSettle();
    expect(find.text('At least 6 characters'), findsWidgets, reason: 'server field error shown on the field');
    await tester.enterText(find.byKey(const ValueKey('staff-password')), 'ali12345');
    await tester.tap(find.byKey(const ValueKey('staff-save')));
    await tester.pumpAndSettle();
    expect(fake.staffWrites.last, {
      'display_name': 'Ali Khan',
      'username': 'ali',
      'password': 'ali12345',
      'roles': ['waiter', 'cashier'],
    });
    expect(find.byKey(const ValueKey('staff-ali')), findsOneWidget);
  });

  testWidgets('ledgers: supplier ledger drills into one supplier and records a bank payment', (tester) async {
    tester.view.physicalSize = const Size(1400, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    app.bootstrap!['permissions'] = ['*'];
    await tester.pumpWidget(host(app, const LedgersScreen()));
    await tester.pumpAndSettle();
    expect(fake.ledgerQueries.last['kind'], 'cash');
    await tester.tap(find.text('Suppliers'));
    await tester.pumpAndSettle();
    expect(fake.ledgerQueries.last['kind'], 'supplier');
    expect(find.text('All suppliers'), findsWidgets);
    expect(find.text('Rs 10,000'), findsWidgets);
    await tester.tap(find.text('Goods received'));
    await tester.pumpAndSettle();
    expect(fake.ledgerQueries.last['supplier'], 'S1');

    await tester.tap(find.byKey(const ValueKey('pay-supplier')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('pay-amount')), '16,000');
    await tester.tap(find.text('Bank transfer'));
    await tester.tap(find.text('Record payment'));
    await tester.pumpAndSettle();
    expect(fake.supplierPayments.last, {'amount_minor': 1600000, 'method': 'bank', 'reference': '', 'note': ''});
  });

  testWidgets('printer setup: add the USB receipt printer picked from the installed list', (tester) async {
    tester.view.physicalSize = const Size(1200, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final (app, fake) = await signedIn();
    await tester.pumpWidget(host(app, const PrintersScreen()));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('add-printer')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('printer-name')), 'Counter');
    await tester.tap(find.byKey(const ValueKey('printer-installed')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('EPSON TM-T88V').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('printer-save')));
    await tester.pumpAndSettle();
    expect(fake.printerWrites.last, {
      'name': 'Counter',
      'purpose': 'receipt',
      'transport': 'system',
      'address': 'EPSON TM-T88V',
      'station_id': null,
      'width_chars': 48,
      'is_active': true,
    });
    expect(find.text('Counter'), findsOneWidget);
  });

  testWidgets('splash shows the brand first, then the sign in screen', (tester) async {
    tester.view.physicalSize = const Size(1400, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    SharedPreferences.setMockInitialValues({'server': 'http://fake'});
    final prefs = await SharedPreferences.getInstance();
    final fake = FakeBackend();
    final app = AppState(
      prefs,
      api: ApiClient(baseUrl: 'http://fake', client: fake.client),
    );
    await tester.pumpWidget(VibeOrderApp(state: app, splash: const Duration(milliseconds: 800)));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byKey(const ValueKey('splash-view')), findsOneWidget);
    expect(find.text('VibeOrder'), findsOneWidget);
    await tester.pump(const Duration(milliseconds: 900));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.byKey(const ValueKey('splash-view')), findsNothing);
    expect(find.byKey(const ValueKey('sign-in')), findsOneWidget);
  });
}
