import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:todo_mobile/services/todo_api.dart';

void main() {
  group('TodoApi', () {
    test('fetchTodos parses the list', () async {
      final api = TodoApi(
        baseUrl: 'http://test',
        client: MockClient((req) async {
          expect(req.method, 'GET');
          expect(req.url.path, '/api/todos');
          return http.Response(
            jsonEncode([
              {'id': 1, 'title': 'Buy milk', 'completed': false},
              {'id': 2, 'title': 'Ship app', 'completed': true},
            ]),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      final todos = await api.fetchTodos();
      expect(todos, hasLength(2));
      expect(todos.first.title, 'Buy milk');
      expect(todos.last.completed, isTrue);
    });

    test('addTodo posts the title and parses the created todo', () async {
      final api = TodoApi(
        baseUrl: 'http://test',
        client: MockClient((req) async {
          expect(req.method, 'POST');
          expect(jsonDecode(req.body), {'title': 'Buy milk'});
          return http.Response(
            jsonEncode({'id': 1, 'title': 'Buy milk', 'completed': false}),
            201,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      final todo = await api.addTodo('Buy milk');
      expect(todo.id, 1);
      expect(todo.completed, isFalse);
    });

    test('addTodo surfaces the server error message', () async {
      final api = TodoApi(
        baseUrl: 'http://test',
        client: MockClient(
          (req) async => http.Response(
            jsonEncode({'error': 'title is required'}),
            400,
            headers: {'content-type': 'application/json'},
          ),
        ),
      );

      expect(
        () => api.addTodo('   '),
        throwsA(
          isA<TodoApiException>()
              .having((e) => e.statusCode, 'statusCode', 400)
              .having((e) => e.message, 'message', 'title is required'),
        ),
      );
    });

    test('setCompleted patches the todo', () async {
      final api = TodoApi(
        baseUrl: 'http://test',
        client: MockClient((req) async {
          expect(req.method, 'PATCH');
          expect(req.url.path, '/api/todos/7');
          expect(jsonDecode(req.body), {'completed': true});
          return http.Response(
            jsonEncode({'id': 7, 'title': 'x', 'completed': true}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }),
      );

      final todo = await api.setCompleted(7, true);
      expect(todo.completed, isTrue);
    });

    test('deleteTodo throws on 404', () async {
      final api = TodoApi(
        baseUrl: 'http://test',
        client: MockClient(
          (req) async => http.Response(
            jsonEncode({'error': 'not found'}),
            404,
            headers: {'content-type': 'application/json'},
          ),
        ),
      );

      expect(
        () => api.deleteTodo(99),
        throwsA(
          isA<TodoApiException>()
              .having((e) => e.statusCode, 'statusCode', 404),
        ),
      );
    });
  });
}
