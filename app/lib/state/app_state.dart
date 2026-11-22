import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_client.dart';

enum Stage { login, ready }

/// Username + password sign in, the operator session and branch bootstrap.
/// There is no pairing: the device registers itself at the first sign in and keeps a random device key.
class AppState extends ChangeNotifier {
  AppState(this.prefs, {ApiClient? api}) : api = api ?? ApiClient(baseUrl: prefs.getString('server') ?? defaultServer) {
    this.api
      ..branchId = prefs.getString('branch_id')
      ..sessionToken = prefs.getString('session_token')
      ..onUnauthorized = _onUnauthorized;
  }

  static const defaultServer = 'http://localhost:8000';

  final SharedPreferences prefs;
  final ApiClient api;

  Map<String, dynamic>? bootstrap;
  Map<String, dynamic>? menu;
  String? lastError;
  bool restoring = false;

  Stage get stage => api.sessionToken == null || bootstrap == null ? Stage.login : Stage.ready;

  /// True when a saved session exists that we have not checked with the server yet.
  bool get hasSavedSession => api.sessionToken != null && bootstrap == null;

  String get server => api.baseUrl;
  String get branchId => api.branchId ?? '';
  String get branchName => bootstrap?['branch']?['name'] ?? prefs.getString('branch_name') ?? '';
  String get staffId => bootstrap?['staff']?['id'] ?? '';
  String get staffName => bootstrap?['staff']?['display_name'] ?? '';
  String get username => bootstrap?['staff']?['username'] ?? '';
  String get lastUsername => prefs.getString('last_username') ?? '';
  String get deviceName => prefs.getString('device_name') ?? defaultDeviceName;
  String get currency => bootstrap?['branch']?['currency'] ?? 'PKR';
  List<String> get roles => List<String>.from(bootstrap?['roles'] ?? const []);
  bool can(String perm) {
    final perms = List<String>.from(bootstrap?['permissions'] ?? const []);
    return perms.contains('*') || perms.contains(perm);
  }

  bool get isCashier => can('tender.take');
  bool get isManager => can('staff.manage');

  static String get defaultDeviceName {
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
      case TargetPlatform.iOS:
        return 'Tablet';
      default:
        return 'Counter PC';
    }
  }

  /// Roles that see at least one alert category (stock, kitchen, print, cash, system).
  bool get seesAlerts => const ['stock.view', 'station.view', 'print.reprint', 'tender.take', 'ops.view'].any(can);

  /// Alert counts by severity from the last `GET alerts` (drives the navigation badge).
  Map<String, int> alertCounts = const {};

  void setAlertCounts(Map<String, dynamic> counts) {
    final next = {for (final e in counts.entries) e.key: (e.value as num).toInt()};
    if (next.toString() == alertCounts.toString()) return;
    alertCounts = next;
    notifyListeners();
  }

  Future<void> refreshAlerts() async {
    if (!seesAlerts || stage != Stage.ready) return;
    try {
      final r = await api.get('alerts');
      setAlertCounts(Map<String, dynamic>.from(r.data['counts']));
    } catch (_) {}
  }

  /// Sign in with username + password. Throws [ChooseBranch] when the person works at several branches
  /// and did not pick one; call again with [branch].
  Future<void> login(String server, String username, String password, {String? branch}) async {
    api.baseUrl = server.trim().isEmpty ? defaultServer : server.trim();
    api.sessionToken = null;
    ApiResult r;
    try {
      r = await api.post('/api/v1/login', {
        'username': username.trim(),
        'password': password,
        'device_name': deviceName,
        'device_kind': 'tablet',
        'device_key': ?prefs.getString('device_key'),
        'branch': ?branch,
      });
    } on ApiError catch (e) {
      if (e.code == 'choose_branch') {
        throw ChooseBranch(List<Map<String, dynamic>>.from(e.body['branches'] ?? const []));
      }
      rethrow;
    }
    api.sessionToken = r.data['session_token'];
    api.branchId = r.data['branch_id'];
    await prefs.setString('server', api.baseUrl);
    await prefs.setString('device_key', r.data['device_key']);
    await prefs.setString('branch_id', api.branchId!);
    await prefs.setString('branch_name', r.data['branch_name'] ?? '');
    await prefs.setString('last_username', username.trim());
    try {
      await refreshBootstrap();
      await prefs.setString('session_token', api.sessionToken!);
    } catch (e) {
      api.sessionToken = null;
      rethrow;
    }
  }

  /// Reopen the app signed in when the saved session is still valid.
  Future<void> restoreSession() async {
    if (!hasSavedSession || restoring) return;
    restoring = true;
    notifyListeners();
    try {
      await refreshBootstrap();
    } on ApiError catch (e) {
      if (!e.uncertain) await _forgetSession();
      lastError = e.uncertain ? e.message : null;
    } finally {
      restoring = false;
      notifyListeners();
    }
  }

  Future<void> changePassword(String current, String next) async {
    await api.post('me/password', {'current_password': current, 'new_password': next});
  }

  Future<void> refreshBootstrap() async {
    bootstrap = (await api.get('bootstrap')).data;
    menu = (await api.get('menu')).data;
    notifyListeners();
  }

  Future<void> refreshMenu() async {
    menu = (await api.get('menu')).data;
    notifyListeners();
  }

  /// Sign out (shared tablets: the next person signs in with their own account).
  Future<void> logout() async {
    try {
      await api.post('sessions/logout', {});
    } catch (_) {}
    await _forgetSession();
  }

  void _onUnauthorized() {
    lastError = 'Session ended. Please sign in again.';
    _forgetSession();
  }

  Future<void> _forgetSession() async {
    api.sessionToken = null;
    bootstrap = null;
    alertCounts = const {};
    await prefs.remove('session_token');
    notifyListeners();
  }

  // ---- local persistence scoped to branch + operator (spec 02: tablet owns drafts) ----

  String _scoped(String kind, String id) => 'vo.$kind.$branchId.$staffId.$id';

  List<Map<String, dynamic>> loadDraft(String checkId) {
    final raw = prefs.getString(_scoped('draft', checkId));
    if (raw == null) return [];
    return List<Map<String, dynamic>>.from(jsonDecode(raw));
  }

  Future<void> saveDraft(String checkId, List<Map<String, dynamic>> items) async {
    if (items.isEmpty) {
      await prefs.remove(_scoped('draft', checkId));
    } else {
      await prefs.setString(_scoped('draft', checkId), jsonEncode(items));
    }
  }

  /// Unacknowledged submission: retried with exactly the same idempotency key.
  Map<String, dynamic>? loadPending(String checkId) {
    final raw = prefs.getString(_scoped('pending', checkId));
    return raw == null ? null : Map<String, dynamic>.from(jsonDecode(raw));
  }

  Future<void> savePending(String checkId, Map<String, dynamic>? pending) async {
    if (pending == null) {
      await prefs.remove(_scoped('pending', checkId));
    } else {
      await prefs.setString(_scoped('pending', checkId), jsonEncode(pending));
    }
  }
}

class ChooseBranch implements Exception {
  ChooseBranch(this.branches);
  final List<Map<String, dynamic>> branches;
}
