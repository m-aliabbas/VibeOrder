import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/app_state.dart';
import 'alerts_screen.dart';
import 'cashier_screen.dart';
import 'dashboard_screen.dart';
import 'ledgers_screen.dart';
import '../widgets/print_log.dart';
import 'devices_screen.dart';
import 'dispatch_screen.dart';
import 'floor_screen.dart';
import 'shift_screen.dart';
import 'staff_screen.dart';
import 'stock_screen.dart';

/// Role-specific landing: waiters get the floor, cashiers get counter + shift.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int? index;
  int stockTab = 0;
  String? pendingDest;
  Timer? alertTimer;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    app.refreshAlerts();
    alertTimer = Timer.periodic(const Duration(seconds: 20), (_) => app.refreshAlerts());
  }

  @override
  void dispose() {
    alertTimer?.cancel();
    super.dispose();
  }

  Widget _icon(Object o) => o is IconData ? Icon(o) : o as Widget;

  Widget _alertIcon(AppState app) {
    final critical = app.alertCounts['critical'] ?? 0;
    final total = app.alertCounts.values.fold(0, (a, b) => a + b);
    final icon = Icon(critical > 0 ? Icons.notifications_active : Icons.notifications_none);
    if (total == 0) return icon;
    return Badge(
      key: const ValueKey('alert-badge'),
      label: Text('${critical > 0 ? critical : total}'),
      backgroundColor: critical > 0 ? Theme.of(context).colorScheme.error : Colors.orange.shade700,
      child: icon,
    );
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    void openStock(int tab) => setState(() {
      stockTab = tab;
      index = null;
      pendingDest = 'Stock';
    });
    final dests = <(Object, String, Widget)>[
      if (app.can('reports.view')) (Icons.insights_outlined, 'Dashboard', const DashboardScreen()),
      if (app.can('reports.view')) (Icons.menu_book_outlined, 'Ledgers', const LedgersScreen()),
      if (app.can('floor.view')) (Icons.table_restaurant, 'Floor', const FloorScreen()),
      if (app.isCashier) (Icons.point_of_sale, 'Counter', const CashierScreen()),
      if (app.can('pack.verify') || app.can('delivery.manage'))
        (Icons.inventory_2_outlined, 'Dispatch', const DispatchScreen()),
      if (app.can('delivery.view_own') && !app.can('delivery.manage'))
        (Icons.two_wheeler, 'My deliveries', const RiderJobsScreen()),
      if (app.can('shift.view')) (Icons.account_balance_wallet_outlined, 'Drawer', const ShiftScreen()),
      if (app.seesAlerts) (_alertIcon(app), 'Alerts', AlertsScreen(onOpenStock: openStock)),
      if (app.can('stock.view'))
        (Icons.inventory_outlined, 'Stock', StockScreen(key: ValueKey('stock-$stockTab'), initialTab: stockTab)),
      if (app.can('print.reprint')) (Icons.receipt_long_outlined, 'KOT & receipts', const PrintLogScreen()),
      if (app.isManager) (Icons.badge_outlined, 'Staff', const StaffScreen()),
      (Icons.print_outlined, 'Devices', const DevicesScreen()),
    ];
    // Role landing: cashiers start at the counter, everyone else at their first screen.
    if (pendingDest != null) {
      index = dests.indexWhere((d) => d.$2 == pendingDest);
      pendingDest = null;
    }
    index ??= app.isManager
        ? dests.indexWhere((d) => d.$2 == 'Dashboard')
        : (app.isCashier ? dests.indexWhere((d) => d.$2 == 'Counter') : 0);
    if (index! < 0 || index! >= dests.length) index = 0;
    final index_ = index!;
    final wide = MediaQuery.sizeOf(context).width >= 700;
    final body = dests[index_].$3;
    final header = _AccountMenu(app: app, compact: false);
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
                    child: _AccountMenu(app: app, compact: true),
                  ),
                ),
              ),
              destinations: [for (final d in dests) NavigationRailDestination(icon: _icon(d.$1), label: Text(d.$2))],
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
              destinations: [for (final d in dests) NavigationDestination(icon: _icon(d.$1), label: d.$2)],
            ),
    );
  }
}

/// Who is signed in, with change password and sign out. Shared tablets: sign out, next person signs in.
class _AccountMenu extends StatelessWidget {
  const _AccountMenu({required this.app, required this.compact});
  final AppState app;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final initial = app.staffName.isEmpty ? '?' : app.staffName[0].toUpperCase();
    final avatar = CircleAvatar(
      radius: 18,
      backgroundColor: t.colorScheme.primary,
      child: Text(
        initial,
        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800),
      ),
    );
    return PopupMenuButton<String>(
      key: const ValueKey('account'),
      tooltip: 'Account',
      position: PopupMenuPosition.under,
      onSelected: (v) {
        if (v == 'password') showChangePassword(context);
        if (v == 'logout') app.logout();
      },
      itemBuilder: (_) => [
        PopupMenuItem(
          enabled: false,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(app.staffName, style: t.textTheme.titleSmall),
              Text('@${app.username} · ${app.branchName}', style: t.textTheme.bodySmall),
            ],
          ),
        ),
        const PopupMenuDivider(),
        const PopupMenuItem(
          value: 'password',
          child: ListTile(leading: Icon(Icons.key_outlined), title: Text('Change my password')),
        ),
        const PopupMenuItem(
          key: ValueKey('logout'),
          value: 'logout',
          child: ListTile(leading: Icon(Icons.logout), title: Text('Sign out')),
        ),
      ],
      child: compact
          ? Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                avatar,
                const SizedBox(height: 4),
                SizedBox(
                  width: 80,
                  child: Text(app.staffName, textAlign: TextAlign.center, style: t.textTheme.labelSmall, maxLines: 2),
                ),
              ],
            )
          : Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(app.staffName, style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(width: 8),
                  avatar,
                ],
              ),
            ),
    );
  }
}
