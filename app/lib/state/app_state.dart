import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/api_client.dart';

enum Stage { setup, login, ready }

/// Device pairing, operator session and branch bootstrap.
class AppState extends ChangeNotifier {
  AppState(this.prefs, {ApiClient? api}) : api = api ?? ApiClient(baseUrl: prefs.getString('server') ?? defaultServer) {
    this.api
      ..deviceToken = prefs.getString('device_token')
      ..branchId = prefs.getString('branch_id')
      ..onUnauthorized = _onUnauthorized;
  }

  static const defaultServer = 'http://localhost:8000';

  final SharedPreferences prefs;
  final ApiClient api;

  Map<String, dynamic>? bootstrap;
  Map<String, dynamic>? menu;
  String? lastError;

  Stage get stage {
    if (api.deviceToken == null || api.branchId == null) return Stage.setup;
    if (api.sessionToken == null || bootstrap == null) return Stage.login;
    return Stage.ready;
  }

  String get server => api.baseUrl;
  String get branchId => api.branchId ?? '';
  String get staffId => bootstrap?['staff']?['id'] ?? '';
  String get staffName => bootstrap?['staff']?['display_name'] ?? '';
  String get deviceName => prefs.getString('device_name') ?? '';
  String get currency => bootstrap?['branch']?['currency'] ?? 'PKR';
  List<String> get roles => List<String>.from(bootstrap?['roles'] ?? const []);
  bool can(String perm) {
    final perms = List<String>.from(bootstrap?['permissions'] ?? const []);
    return perms.contains('*') || perms.contains(perm);
  }

  bool get isCashier => can('tender.take');

  Future<void> pair(String server, String code, String name) async {
    api.baseUrl = server.trim();
    api.deviceToken = null;
    final r = await api.post('/api/v1/pair', {'code': code.trim(), 'name': name});
    api.deviceToken = r.data['device_token'];
    api.branchId = r.data['branch_id'];
    await prefs.setString('server', api.baseUrl);
    await prefs.setString('device_token', api.deviceToken!);
    await prefs.setString('branch_id', api.branchId!);
    await prefs.setString('device_name', name);
    await prefs.setString('branch_name', r.data['branch_name'] ?? '');
    notifyListeners();
  }

  Future<List<Map<String, dynamic>>> staffDirectory() async {
    final r = await api.get('staff-directory');
    return List<Map<String, dynamic>>.from(r.data['staff']);
  }

  Future<void> login(String username, String pin) async {
    final r = await api.post('sessions', {'username': username, 'pin': pin});
    api.sessionToken = r.data['session_token'];
    try {
      await refreshBootstrap();
    } catch (e) {
      api.sessionToken = null;
      rethrow;
    }
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

  /// Shared-device lock: drop the operator session but keep the device paired.
  Future<void> lock() async {
    try {
      await api.post('sessions/logout', {});
    } catch (_) {}
    _clearSession();
  }

  void _onUnauthorized() {
    lastError = 'Session ended. Please sign in again.';
    _clearSession();
  }

  void _clearSession() {
    api.sessionToken = null;
    bootstrap = null;
    notifyListeners();
  }

  Future<void> unpair() async {
    for (final k in ['device_token', 'branch_id', 'device_name']) {
      await prefs.remove(k);
    }
    api
      ..deviceToken = null
      ..branchId = null
      ..sessionToken = null;
    bootstrap = null;
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
