package co.thehindu.todonative

import org.json.JSONObject

data class Todo(
    val id: Int,
    val title: String,
    val completed: Boolean,
) {
    companion object {
        fun fromJson(json: JSONObject): Todo = Todo(
            id = json.getInt("id"),
            title = json.getString("title"),
            completed = json.optBoolean("completed", false),
        )
    }
}
