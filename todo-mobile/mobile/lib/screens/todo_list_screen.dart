import 'package:flutter/material.dart';

import '../models/todo.dart';
import '../services/todo_api.dart';

class TodoListScreen extends StatefulWidget {
  final TodoApi api;

  const TodoListScreen({super.key, required this.api});

  @override
  State<TodoListScreen> createState() => _TodoListScreenState();
}

class _TodoListScreenState extends State<TodoListScreen> {
  final _titleController = TextEditingController();
  List<Todo> _todos = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _titleController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final todos = await widget.api.fetchTodos();
      if (!mounted) return;
      setState(() {
        _todos = todos;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Could not load todos. Pull down to retry.';
      });
    }
  }

  Future<void> _runAction(Future<void> Function() action) async {
    try {
      await action();
      await _load();
    } on TodoApiException catch (e) {
      _showError(e.message);
    } catch (_) {
      _showError('Something went wrong. Please try again.');
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _addTodo() async {
    final title = _titleController.text.trim();
    if (title.isEmpty) return;
    await _runAction(() async {
      await widget.api.addTodo(title);
      _titleController.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Todo App'),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Row(
              children: [
                Expanded(
                  child: Semantics(
                    // identifier → Android resource-id / iOS accessibilityIdentifier.
                    // A `label:` on an editable field does NOT become content-desc —
                    // it merges into the hint text, so accessibility-id lookups
                    // ("~new-todo-input") can never find a TextField.
                    identifier: 'new-todo-input',
                    child: TextField(
                      controller: _titleController,
                      decoration: const InputDecoration(
                        hintText: 'What needs to be done?',
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _addTodo(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Semantics(
                  label: 'add-todo-button',
                  button: true,
                  child: FilledButton(
                    onPressed: _addTodo,
                    child: const Text('Add', semanticsLabel: ''),
                  ),
                ),
              ],
            ),
          ),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: _todos.isEmpty ? _buildEmptyState() : _buildList(),
    );
  }

  // Wrapped in a scrollable so RefreshIndicator works on the empty state too.
  Widget _buildEmptyState() {
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        child: SizedBox(
          height: constraints.maxHeight,
          child: Center(
            child: Text(
              _error ?? 'No todos yet. Add one above!',
              style: TextStyle(color: Theme.of(context).hintColor),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildList() {
    return ListView.separated(
      physics: const AlwaysScrollableScrollPhysics(),
      itemCount: _todos.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final todo = _todos[index];
        return ListTile(
          leading: Semantics(
            label: 'Toggle ${todo.title}',
            child: Checkbox(
              value: todo.completed,
              onChanged: (checked) => _runAction(
                () => widget.api.setCompleted(todo.id, checked ?? false),
              ),
            ),
          ),
          title: Text(
            todo.title,
            style: todo.completed
                ? TextStyle(
                    decoration: TextDecoration.lineThrough,
                    color: Theme.of(context).disabledColor,
                  )
                : null,
          ),
          trailing: IconButton(
            icon: const Icon(Icons.close),
            color: Theme.of(context).colorScheme.error,
            tooltip: 'Delete ${todo.title}',
            onPressed: () => _runAction(() => widget.api.deleteTodo(todo.id)),
          ),
        );
      },
    );
  }
}
