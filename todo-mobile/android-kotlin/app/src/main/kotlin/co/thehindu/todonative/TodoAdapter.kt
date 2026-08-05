package co.thehindu.todonative

import android.graphics.Paint
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.ImageButton
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class TodoAdapter(
    private val onToggle: (Todo, Boolean) -> Unit,
    private val onDelete: (Todo) -> Unit,
) : RecyclerView.Adapter<TodoAdapter.TodoViewHolder>() {

    private var todos: List<Todo> = emptyList()

    @Suppress("NotifyDataSetChanged") // list is tiny; server is the source of truth
    fun submit(newTodos: List<Todo>) {
        todos = newTodos
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): TodoViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_todo, parent, false)
        return TodoViewHolder(view)
    }

    override fun getItemCount(): Int = todos.size

    override fun onBindViewHolder(holder: TodoViewHolder, position: Int) =
        holder.bind(todos[position])

    inner class TodoViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        private val toggle: CheckBox = view.findViewById(R.id.todo_toggle)
        private val title: TextView = view.findViewById(R.id.todo_title)
        private val delete: ImageButton = view.findViewById(R.id.delete_todo)

        fun bind(todo: Todo) {
            title.text = todo.title
            // content-desc mirrors the Flutter app so Appium locators
            // (~<title>, ~Toggle <title>, ~Delete <title>) work identically.
            title.contentDescription = todo.title
            title.paintFlags = if (todo.completed) {
                title.paintFlags or Paint.STRIKE_THRU_TEXT_FLAG
            } else {
                title.paintFlags and Paint.STRIKE_THRU_TEXT_FLAG.inv()
            }
            title.alpha = if (todo.completed) 0.5f else 1f

            toggle.setOnCheckedChangeListener(null)
            toggle.isChecked = todo.completed
            toggle.contentDescription = "Toggle ${todo.title}"
            toggle.setOnCheckedChangeListener { _, checked -> onToggle(todo, checked) }

            delete.contentDescription = "Delete ${todo.title}"
            delete.setOnClickListener { onDelete(todo) }
        }
    }
}
