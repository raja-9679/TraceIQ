package co.thehindu.todonative

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

class TodoApiException(val statusCode: Int, message: String) : Exception(message)

/** Client for the Express todo REST API (server.js). */
class TodoApi(
    private val baseUrl: String,
    private val client: OkHttpClient = OkHttpClient(),
) {
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun fetchTodos(): List<Todo> = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("$baseUrl/api/todos").get().build()
        client.newCall(request).execute().use { res ->
            val text = res.body?.string()
            ensure(res.code, 200, text)
            val arr = JSONArray(text!!.ifEmpty { "[]" })
            (0 until arr.length()).map { Todo.fromJson(arr.getJSONObject(it)) }
        }
    }

    suspend fun addTodo(title: String): Todo = withContext(Dispatchers.IO) {
        val body = JSONObject().put("title", title).toString().toRequestBody(jsonMediaType)
        val request = Request.Builder().url("$baseUrl/api/todos").post(body).build()
        client.newCall(request).execute().use { res ->
            val text = res.body?.string()
            ensure(res.code, 201, text)
            Todo.fromJson(JSONObject(text!!))
        }
    }

    suspend fun setCompleted(id: Int, completed: Boolean): Todo = withContext(Dispatchers.IO) {
        val body = JSONObject().put("completed", completed).toString().toRequestBody(jsonMediaType)
        val request = Request.Builder().url("$baseUrl/api/todos/$id").patch(body).build()
        client.newCall(request).execute().use { res ->
            val text = res.body?.string()
            ensure(res.code, 200, text)
            Todo.fromJson(JSONObject(text!!))
        }
    }

    suspend fun deleteTodo(id: Int): Unit = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("$baseUrl/api/todos/$id").delete().build()
        client.newCall(request).execute().use { res ->
            ensure(res.code, 204, res.body?.string())
        }
    }

    private fun ensure(actual: Int, expected: Int, body: String?) {
        if (actual != expected) {
            val message = try {
                JSONObject(body ?: "").optString("error", body ?: "")
            } catch (_: Exception) {
                body ?: ""
            }
            throw TodoApiException(actual, message)
        }
    }
}
