import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:todo_mobile/screens/todo_list_screen.dart';
import 'package:todo_mobile/services/todo_api.dart';

/// In-memory fake of the Express server so the whole screen can be exercised.
MockClient fakeServer() {
  var nextId = 1;
  final todos = <Map<String, dynamic>>[];

  return MockClient((req) async {
    final path = req.url.path;
    if (req.method == 'GET' && path == '/api/todos') {
      return http.Response(jsonEncode(todos), 200,
          headers: {'content-type': 'application/json'});
    }
    if (req.method == 'POST' && path == '/api/todos') {
      final title = (jsonDecode(req.body)['title'] as String? ?? '').trim();
      if (title.isEmpty) {
        return http.Response(jsonEncode({'error': 'title is required'}), 400,
            headers: {'content-type': 'application/json'});
      }
      final todo = {'id': nextId++, 'title': title, 'completed': false};
      todos.add(todo);
      return http.Response(jsonEncode(todo), 201,
          headers: {'content-type': 'application/json'});
    }
    final idMatch = RegExp(r'^/api/todos/(\d+)$').firstMatch(path);
    if (idMatch != null) {
      final id = int.parse(idMatch.group(1)!);
      final idx = todos.indexWhere((t) => t['id'] == id);
      if (idx == -1) {
        return http.Response(jsonEncode({'error': 'not found'}), 404,
            headers: {'content-type': 'application/json'});
      }
      if (req.method == 'PATCH') {
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        if (body['completed'] is bool) {
          todos[idx]['completed'] = body['completed'];
        }
        return http.Response(jsonEncode(todos[idx]), 200,
            headers: {'content-type': 'application/json'});
      }
      if (req.method == 'DELETE') {
        todos.removeAt(idx);
        return http.Response('', 204);
      }
    }
    return http.Response('not found', 404);
  });
}

Widget buildApp() => MaterialApp(
      home: TodoListScreen(
        api: TodoApi(baseUrl: 'http://test', client: fakeServer()),
      ),
    );

void main() {
  testWidgets('shows the empty state initially', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();

    expect(find.text('No todos yet. Add one above!'), findsOneWidget);
  });

  testWidgets('adds a todo and clears the input', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Buy milk');
    await tester.tap(find.text('Add'));
    await tester.pumpAndSettle();

    expect(find.text('Buy milk'), findsOneWidget);
    expect(find.text('No todos yet. Add one above!'), findsNothing);
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        isEmpty);
  });

  testWidgets('does not add a blank todo', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), '   ');
    await tester.tap(find.text('Add'));
    await tester.pumpAndSettle();

    expect(find.text('No todos yet. Add one above!'), findsOneWidget);
  });

  testWidgets('toggles a todo to completed (strikethrough)', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Buy milk');
    await tester.tap(find.text('Add'));
    await tester.pumpAndSettle();

    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();

    final checkbox = tester.widget<Checkbox>(find.byType(Checkbox));
    expect(checkbox.value, isTrue);
    final title = tester.widget<Text>(find.text('Buy milk'));
    expect(title.style?.decoration, TextDecoration.lineThrough);
  });

  testWidgets('deletes a todo and returns to the empty state', (tester) async {
    await tester.pumpWidget(buildApp());
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Buy milk');
    await tester.tap(find.text('Add'));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Delete Buy milk'));
    await tester.pumpAndSettle();

    expect(find.text('Buy milk'), findsNothing);
    expect(find.text('No todos yet. Add one above!'), findsOneWidget);
  });
}
