import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

/// Shared error envelope: code, message, field_errors, current_revision, correlation_id.
class ApiError implements Exception {
  ApiError(this.status, this.code, this.message, {this.body = const {}});

  final int status;
  final String code;
  final String message;
  final Map<String, dynamic> body;

  int? get currentRevision => body['current_revision'] as int?;

  /// True when we cannot know whether the server committed (timeout, socket drop).
  bool get uncertain => status == 0;

  @override
  String toString() => message;
}

class ApiResult {
  ApiResult(this.status, this.data, {this.replayed = false});
  final int status;
  final Map<String, dynamic> data;
  final bool replayed;
}

/// Thin REST client. REST is authoritative; never guesses whether a write succeeded.
class ApiClient {
  ApiClient({required this.baseUrl, this.deviceToken, this.sessionToken, this.branchId, http.Client? client})
    : _http = client ?? http.Client();

  String baseUrl;
  String? deviceToken;
  String? sessionToken;
  String? branchId;
  final http.Client _http;
  static const _uuid = Uuid();
  Duration timeout = const Duration(seconds: 10);

  /// Called when the server rejects the session (expired, revoked device).
  void Function()? onUnauthorized;

  static String newKey() => _uuid.v4();

  Uri _uri(String path, [Map<String, String>? query]) {
    final p = path.startsWith('/') ? path : '/api/v1/branches/$branchId/$path';
    final base = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$base$p').replace(queryParameters: query);
  }

  Map<String, String> _headers({String? key}) => {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'X-Device-Token': ?deviceToken,
    if (sessionToken != null) 'Authorization': 'Bearer $sessionToken',
    'Idempotency-Key': ?key,
  };

  Future<ApiResult> get(String path, {Map<String, String>? query}) =>
      _send(() => _http.get(_uri(path, query), headers: _headers()));

  /// Business command. [key] must be persisted by the caller before sending when the
  /// intent must survive a restart; retrying with the same key returns the original result.
  Future<ApiResult> command(String path, Map<String, dynamic> body, {String? key}) {
    final k = key ?? newKey();
    return _send(
      () => _http.post(
        _uri(path),
        headers: _headers(key: k),
        body: jsonEncode(body),
      ),
    );
  }

  /// Non-idempotent auxiliary POST (pairing, login, quotes).
  Future<ApiResult> post(String path, Map<String, dynamic> body) =>
      _send(() => _http.post(_uri(path), headers: _headers(), body: jsonEncode(body)));

  Future<ApiResult> _send(Future<http.Response> Function() fn) async {
    http.Response res;
    try {
      res = await fn().timeout(timeout);
    } on TimeoutException {
      throw ApiError(0, 'timeout', 'No answer from the branch server. Your request may or may not have been saved.');
    } catch (e) {
      throw ApiError(0, 'network', 'Cannot reach the branch server.');
    }
    Map<String, dynamic> data = {};
    if (res.body.isNotEmpty) {
      try {
        final decoded = jsonDecode(utf8.decode(res.bodyBytes));
        if (decoded is Map<String, dynamic>) data = decoded;
      } catch (_) {}
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return ApiResult(res.statusCode, data, replayed: res.headers['idempotent-replay'] == 'true');
    }
    if (res.statusCode == 401 && sessionToken != null) onUnauthorized?.call();
    throw ApiError(
      res.statusCode,
      (data['code'] ?? 'error').toString(),
      (data['message'] ?? 'Request failed (${res.statusCode})').toString(),
      body: data,
    );
  }
}
