package co.thehindu.todonative

import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private val api = TodoApi(BuildConfig.API_BASE_URL)

    private lateinit var input: EditText
    private lateinit var addButton: Button
    private lateinit var list: RecyclerView
    private lateinit var emptyState: TextView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private lateinit var adapter: TodoAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        input = findViewById(R.id.new_todo_input)
        addButton = findViewById(R.id.add_todo_button)
        list = findViewById(R.id.todo_list)
        emptyState = findViewById(R.id.empty_state)
        swipeRefresh = findViewById(R.id.swipe_refresh)

        adapter = TodoAdapter(
            onToggle = { todo, checked -> runAction { api.setCompleted(todo.id, checked) } },
            onDelete = { todo -> runAction { api.deleteTodo(todo.id) } },
        )
        list.layoutManager = LinearLayoutManager(this)
        list.addItemDecoration(DividerItemDecoration(this, DividerItemDecoration.VERTICAL))
        list.adapter = adapter

        addButton.setOnClickListener { addTodo() }
        input.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                addTodo()
                true
            } else {
                false
            }
        }
        swipeRefresh.setOnRefreshListener { load() }

        load()
    }

    private fun addTodo() {
        val title = input.text.toString().trim()
        if (title.isEmpty()) return
        runAction {
            api.addTodo(title)
            input.text.clear()
        }
    }

    private fun runAction(action: suspend () -> Unit) {
        lifecycleScope.launch {
            try {
                action()
                refresh()
            } catch (e: TodoApiException) {
                showError(e.message ?: getString(R.string.generic_error))
            } catch (_: Exception) {
                showError(getString(R.string.generic_error))
            }
        }
    }

    private fun load() {
        lifecycleScope.launch { refresh() }
    }

    private suspend fun refresh() {
        try {
            val todos = api.fetchTodos()
            adapter.submit(todos)
            emptyState.visibility = if (todos.isEmpty()) TextView.VISIBLE else TextView.GONE
            emptyState.text = getString(R.string.empty_state)
        } catch (_: Exception) {
            adapter.submit(emptyList())
            emptyState.visibility = TextView.VISIBLE
            emptyState.text = getString(R.string.load_error)
        } finally {
            swipeRefresh.isRefreshing = false
        }
    }

    private fun showError(message: String) {
        Snackbar.make(list, message, Snackbar.LENGTH_LONG).show()
    }
}
