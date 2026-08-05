import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import 'screens/todo_list_screen.dart';
import 'services/todo_api.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // Build the semantics tree unconditionally. Appium's UiAutomator2 driver
  // finds elements through the Android accessibility tree, and Flutter only
  // populates it when something asks — without this, every Semantics label
  // in the app is invisible to the test runner (accessibility id lookups
  // time out even though the widgets are on screen).
  SemanticsBinding.instance.ensureSemantics();
  runApp(const TodoApp());
}

class TodoApp extends StatelessWidget {
  const TodoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Todo App',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2563EB)),
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2563EB),
          brightness: Brightness.dark,
        ),
      ),
      home: TodoListScreen(api: TodoApi()),
    );
  }
}
