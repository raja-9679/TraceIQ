import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/todo.dart';

/// Thrown when the server responds with a non-success status code.
class TodoApiException implements Exception {
  final int statusCode;
  final String message;

  TodoApiException(this.statusCode, this.message);

  @override
  String toString() => 'TodoApiException($statusCode): $message';
}

/// Client for the Express todo REST API (server.js).
///
/// The base URL defaults to the Android emulator's host loopback. Override at
/// build time with: flutter run --dart-define=API_BASE_URL=http://host:3000
class TodoApi {
  static const defaultBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:3000',
  );

  final String baseUrl;
  final http.Client _client;

  TodoApi({this.baseUrl = defaultBaseUrl, http.Client? client})
      : _client = client ?? http.Client();

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  static const _jsonHeaders = {'Content-Type': 'application/json'};

  Future<List<Todo>> fetchTodos() async {
    final res = await _client.get(_uri('/api/todos'));
    _ensure(res, 200);
    final data = jsonDecode(res.body) as List<dynamic>;
    return data
        .map((e) => Todo.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<Todo> addTodo(String title) async {
    final res = await _client.post(
      _uri('/api/todos'),
      headers: _jsonHeaders,
      body: jsonEncode({'title': title}),
    );
    _ensure(res, 201);
    return Todo.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<Todo> setCompleted(int id, bool completed) async {
    final res = await _client.patch(
      _uri('/api/todos/$id'),
      headers: _jsonHeaders,
      body: jsonEncode({'completed': completed}),
    );
    _ensure(res, 200);
    return Todo.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<void> deleteTodo(int id) async {
    final res = await _client.delete(_uri('/api/todos/$id'));
    _ensure(res, 204);
  }

  void _ensure(http.Response res, int expected) {
    if (res.statusCode != expected) {
      String message = res.body;
      try {
        final decoded = jsonDecode(res.body);
        if (decoded is Map<String, dynamic> && decoded['error'] is String) {
          message = decoded['error'] as String;
        }
      } catch (_) {}
      throw TodoApiException(res.statusCode, message);
    }
  }

  void dispose() => _client.close();
}
