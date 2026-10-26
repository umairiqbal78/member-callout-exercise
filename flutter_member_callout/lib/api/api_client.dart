import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

import '../models/announcement.dart';
import '../models/inbox_item.dart';
import '../models/user_profile.dart';

class ApiException implements Exception {
  final String message;
  final int? statusCode;
  final String? errorCode;

  ApiException(this.message, {this.statusCode, this.errorCode});

  @override
  String toString() => message;
}

class ApiClient {
  String baseUrl;
  String? _token;
  void Function()? onUnauthorized;
  final http.Client _httpClient;

  static const Duration timeoutDuration = Duration(seconds: 15);

  ApiClient({
    String? baseUrl,
    this.onUnauthorized,
    http.Client? httpClient,
  })  : baseUrl = (baseUrl ?? 'http://localhost:8000').replaceAll(RegExp(r'/+$'), ''),
        _httpClient = httpClient ?? http.Client();

  String? get token => _token;
  bool get isAuthenticated => _token != null;

  void setToken(String? token) {
    _token = token;
  }

  void logout() {
    _token = null;
    onUnauthorized?.call();
  }

  Map<String, String> _headers({String? idempotencyKey, bool isJson = true}) {
    final headers = <String, String>{};
    if (isJson) {
      headers['Content-Type'] = 'application/json';
    }
    if (_token != null && _token!.isNotEmpty) {
      headers['Authorization'] = 'Token $_token';
    }
    if (idempotencyKey != null && idempotencyKey.isNotEmpty) {
      headers['Idempotency-Key'] = idempotencyKey;
    }
    return headers;
  }

  Never _handleError(http.Response response) {
    final status = response.statusCode;
    String? errorCode;
    String? detail;

    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, dynamic>) {
        errorCode = decoded['error'] as String?;
        detail = decoded['detail'] as String? ?? decoded['message'] as String?;
      }
    } catch (_) {
      // Body is not JSON
    }

    if (status == 401) {
      logout();
      throw ApiException('Session expired. Please log in again.', statusCode: 401, errorCode: errorCode);
    } else if (status == 409) {
      if (errorCode == 'approval_required') {
        throw ApiException('Approval is required before this announcement can be sent.', statusCode: 409, errorCode: errorCode);
      } else if (errorCode == 'callout_closed') {
        throw ApiException('This callout has been closed.', statusCode: 409, errorCode: errorCode);
      }
      throw ApiException(detail ?? 'Conflict error ($status).', statusCode: 409, errorCode: errorCode);
    } else if (status == 429) {
      throw ApiException('Too many requests. Please wait a moment and try again.', statusCode: 429, errorCode: errorCode);
    } else if (status == 502) {
      throw ApiException('Bad Gateway (502). Upstream service is temporarily unavailable.', statusCode: 502, errorCode: errorCode);
    } else if (status == 503) {
      if (errorCode == 'ai_unavailable') {
        throw ApiException('AI service is currently unavailable. Please draft manually.', statusCode: 503, errorCode: errorCode);
      } else if (errorCode == 'ai_not_configured') {
        throw ApiException('AI service is not configured. Please draft manually.', statusCode: 503, errorCode: errorCode);
      }
      throw ApiException(detail ?? 'Service temporarily unavailable (503).', statusCode: 503, errorCode: errorCode);
    } else {
      throw ApiException(detail ?? 'Request failed ($status): ${response.body}', statusCode: status, errorCode: errorCode);
    }
  }

  Future<dynamic> _wrapRequest(Future<http.Response> Function() req) async {
    try {
      final response = await req().timeout(timeoutDuration);
      if (response.statusCode >= 200 && response.statusCode < 300) {
        if (response.body.isEmpty) return null;
        return jsonDecode(response.body);
      }
      _handleError(response);
    } on TimeoutException {
      throw ApiException('Request timed out after 15 seconds. Please check your connection.');
    } on ApiException {
      rethrow;
    } catch (e) {
      throw ApiException('Network error: Unable to reach server at $baseUrl ($e)');
    }
  }

  // Auth
  Future<String> login(String username, String password) async {
    final uri = Uri.parse('$baseUrl/api/auth/login/');
    final body = jsonEncode({'username': username.trim(), 'password': password.trim()});
    final data = await _wrapRequest(() => _httpClient.post(uri, headers: _headers(isJson: true), body: body));
    final token = data['token'] as String;
    _token = token;
    return token;
  }

  // Me
  Future<UserProfile> getMe() async {
    final uri = Uri.parse('$baseUrl/api/me/');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));
    return UserProfile.fromJson(data as Map<String, dynamic>);
  }

  // Classifications
  Future<List<String>> getClassifications(String localId) async {
    final uri = Uri.parse('$baseUrl/api/classifications/?local_id=$localId');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));
    if (data is List) {
      return data.map((e) => e.toString()).toList();
    }
    return [];
  }

  // Announcements List
  Future<List<AnnouncementItem>> getAnnouncements() async {
    final uri = Uri.parse('$baseUrl/api/announcements/');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));
    final results = (data is Map && data.containsKey('results')) ? data['results'] as List : data as List;
    return results.map((e) => AnnouncementItem.fromJson(e as Map<String, dynamic>)).toList();
  }

  // Announcement Detail
  Future<AnnouncementItem> getAnnouncement(String id) async {
    final uri = Uri.parse('$baseUrl/api/announcements/$id/');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));
    return AnnouncementItem.fromJson(data as Map<String, dynamic>);
  }

  // Stats
  Future<AnnouncementStats> getStats(String id) async {
    final uri = Uri.parse('$baseUrl/api/announcements/$id/stats/');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));
    return AnnouncementStats.fromJson(data as Map<String, dynamic>);
  }

  // AI Draft
  Future<AIDraftResult> draftWithAI(String localId, String note, {String? idempotencyKey}) async {
    final uri = Uri.parse('$baseUrl/api/announcements/ai-draft/');
    final body = jsonEncode({'local_id': localId, 'note': note});
    final data = await _wrapRequest(() => _httpClient.post(
          uri,
          headers: _headers(idempotencyKey: idempotencyKey),
          body: body,
        ));
    return AIDraftResult.fromJson(data as Map<String, dynamic>);
  }

  // Save Draft (Create or Patch)
  Future<AnnouncementItem> saveDraft({
    String? id,
    required String title,
    required String body,
    String? pushPreview,
    String? source,
    String? aiNote,
    String? aiModel,
    List<String>? audienceClassifications,
    String? idempotencyKey,
  }) async {
    if (id == null) {
      final uri = Uri.parse('$baseUrl/api/announcements/');
      final payload = <String, dynamic>{
        'title': title,
        'body': body,
        'push_preview': pushPreview ?? '',
        'source': source ?? 'manual',
        'needs_ack': true,
        'audience_classifications': audienceClassifications ?? [],
      };
      if (aiNote != null) payload['ai_note'] = aiNote;
      if (aiModel != null) payload['ai_model'] = aiModel;

      final data = await _wrapRequest(() => _httpClient.post(
            uri,
            headers: _headers(idempotencyKey: idempotencyKey),
            body: jsonEncode(payload),
          ));
      return AnnouncementItem.fromJson(data as Map<String, dynamic>);
    } else {
      final uri = Uri.parse('$baseUrl/api/announcements/$id/');
      final payload = <String, dynamic>{
        'title': title,
        'body': body,
        'push_preview': pushPreview ?? '',
        'audience_classifications': audienceClassifications ?? [],
      };
      final data = await _wrapRequest(() => _httpClient.patch(
            uri,
            headers: _headers(),
            body: jsonEncode(payload),
          ));
      return AnnouncementItem.fromJson(data as Map<String, dynamic>);
    }
  }

  // Approve
  Future<AnnouncementItem> approve(String id) async {
    final uri = Uri.parse('$baseUrl/api/announcements/$id/approve/');
    final data = await _wrapRequest(() => _httpClient.post(uri, headers: _headers()));
    return AnnouncementItem.fromJson(data as Map<String, dynamic>);
  }

  // Send
  Future<Map<String, dynamic>> send(String id, {String? idempotencyKey}) async {
    final uri = Uri.parse('$baseUrl/api/announcements/$id/send/');
    final data = await _wrapRequest(() => _httpClient.post(
          uri,
          headers: _headers(idempotencyKey: idempotencyKey),
        ));
    return (data as Map<String, dynamic>?) ?? {'status': 'sending'};
  }

  // Inbox
  Future<PaginatedInbox> getInbox({String? cursorUrl}) async {
    final uri = cursorUrl != null ? Uri.parse(cursorUrl) : Uri.parse('$baseUrl/api/me/inbox/');
    final data = await _wrapRequest(() => _httpClient.get(uri, headers: _headers()));

    List<dynamic> resultsRaw;
    String? next;
    if (data is Map<String, dynamic> && data.containsKey('results')) {
      resultsRaw = data['results'] as List<dynamic>? ?? [];
      next = data['next'] as String?;
    } else if (data is List) {
      resultsRaw = data;
    } else {
      resultsRaw = [];
    }

    final items = resultsRaw
        .map((e) => InboxItem.fromJson(e as Map<String, dynamic>))
        .toList();
    return PaginatedInbox(items: items, nextCursor: next);
  }

  // Receipts
  Future<int> sendReceipts(List<String> announcementIds, {String kind = 'delivered'}) async {
    final uri = Uri.parse('$baseUrl/api/me/receipts/');
    final body = jsonEncode({
      'announcement_ids': announcementIds,
      'kind': kind,
    });
    final data = await _wrapRequest(() => _httpClient.post(uri, headers: _headers(), body: body));
    return (data is Map && data.containsKey('updated')) ? (data['updated'] as int) : 0;
  }

  // Acknowledge
  Future<void> acknowledge(String announcementId) async {
    final uri = Uri.parse('$baseUrl/api/me/announcements/$announcementId/acknowledge/');
    await _wrapRequest(() => _httpClient.post(uri, headers: _headers()));
  }

  // Respond
  Future<void> respond(String announcementId, String responseValue) async {
    final uri = Uri.parse('$baseUrl/api/me/announcements/$announcementId/respond/');
    final body = jsonEncode({'response': responseValue});
    await _wrapRequest(() => _httpClient.post(uri, headers: _headers(), body: body));
  }
}
