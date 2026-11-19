import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import 'cashier_screen.dart';
import '../widgets/print_log.dart';
import 'devices_screen.dart';
import 'dispatch_screen.dart';
import 'floor_screen.dart';
import 'shift_screen.dart';
import 'stock_screen.dart';

/// Role-specific landing: waiters get the floor, cashiers get counter + shift.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int? index;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final dests = <(IconData, String, Widget)>[
      if (app.can('floor.view')) (Icons.table_restaurant, 'Floor', const FloorScreen()),
      if (app.isCashier) (Icons.point_of_sale, 'Counter', const CashierScreen()),
      if (app.can('pack.verify') || app.can('delivery.manage'))
        (Icons.inventory_2_outlined, 'Dispatch', const DispatchScreen()),
      if (app.can('delivery.view_own') && !app.can('delivery.manage'))
        (Icons.two_wheeler, 'My deliveries', const RiderJobsScreen()),
      if (app.can('shift.view')) (Icons.account_balance_wallet_outlined, 'Drawer', const ShiftScreen()),
      if (app.can('stock.view')) (Icons.inventory_outlined, 'Stock', const StockScreen()),
      if (app.can('print.reprint')) (Icons.receipt_long_outlined, 'KOT & receipts', const PrintLogScreen()),
      (Icons.print_outlined, 'Devices', const DevicesScreen()),
    ];
    // Role landing: cashiers start at the counter, everyone else at their first screen.
    index ??= app.isCashier ? dests.indexWhere((d) => d.$2 == 'Counter') : 0;
    if (index! < 0 || index! >= dests.length) index = 0;
    final index_ = index!;
    final wide = MediaQuery.sizeOf(context).width >= 700;
    final body = dests[index_].$3;
    final header = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(app.staffName, style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(width: 4),
        IconButton(
          key: const ValueKey('lock'),
          tooltip: 'Lock / switch user',
          icon: const Icon(Icons.lock_outline),
          onPressed: () => app.lock(),
        ),
      ],
    );
    if (wide) {
      return Scaffold(
        body: Row(
          children: [
            NavigationRail(
              selectedIndex: index_,
              onDestinationSelected: (i) => setState(() => index = i),
              labelType: NavigationRailLabelType.all,
              leading: Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Icon(Icons.local_pizza_rounded, color: Theme.of(context).colorScheme.primary, size: 32),
              ),
              trailing: Expanded(
                child: Align(
                  alignment: Alignment.bottomCenter,
                  child: Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          key: const ValueKey('lock'),
                          tooltip: 'Lock / switch user',
                          onPressed: () => app.lock(),
                          icon: const Icon(Icons.lock_outline),
                        ),
                        SizedBox(
                          width: 80,
                          child: Text(
                            app.staffName,
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.labelSmall,
                            maxLines: 2,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              destinations: [for (final d in dests) NavigationRailDestination(icon: Icon(d.$1), label: Text(d.$2))],
            ),
            const VerticalDivider(width: 1),
            Expanded(child: body),
          ],
        ),
      );
    }
    return Scaffold(
      appBar: AppBar(title: Text(dests[index_].$2), actions: [header]),
      body: body,
      bottomNavigationBar: dests.length < 2
          ? null
          : NavigationBar(
              selectedIndex: index_,
              onDestinationSelected: (i) => setState(() => index = i),
              destinations: [for (final d in dests) NavigationDestination(icon: Icon(d.$1), label: d.$2)],
            ),
    );
  }
}
