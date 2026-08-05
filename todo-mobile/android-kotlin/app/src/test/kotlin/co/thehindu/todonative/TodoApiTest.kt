package co.thehindu.todonative

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class TodoApiTest {

    private lateinit var server: MockWebServer
    private lateinit var api: TodoApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = TodoApi(server.url("/").toString().removeSuffix("/"))
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun json(body: String, code: Int = 200): MockResponse =
        MockResponse().setResponseCode(code)
            .setHeader("Content-Type", "application/json")
            .setBody(body)

    @Test
    fun `fetchTodos parses the list`() = runTest {
        server.enqueue(
            json("""[{"id":1,"title":"Buy milk","completed":false},{"id":2,"title":"Ship app","completed":true}]"""),
        )

        val todos = api.fetchTodos()

        assertEquals(2, todos.size)
        assertEquals("Buy milk", todos[0].title)
        assertTrue(todos[1].completed)
        assertEquals("/api/todos", server.takeRequest().path)
    }

    @Test
    fun `addTodo posts the title and parses the created todo`() = runTest {
        server.enqueue(json("""{"id":1,"title":"Buy milk","completed":false}""", 201))

        val todo = api.addTodo("Buy milk")

        assertEquals(1, todo.id)
        assertFalse(todo.completed)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("Buy milk", JSONObject(request.body.readUtf8()).getString("title"))
    }

    @Test
    fun `addTodo surfaces the server error message`() = runTest {
        server.enqueue(json("""{"error":"title is required"}""", 400))

        try {
            api.addTodo("   ")
            fail("expected TodoApiException")
        } catch (e: TodoApiException) {
            assertEquals(400, e.statusCode)
            assertEquals("title is required", e.message)
        }
    }

    @Test
    fun `setCompleted patches the todo`() = runTest {
        server.enqueue(json("""{"id":7,"title":"x","completed":true}"""))

        val todo = api.setCompleted(7, true)

        assertTrue(todo.completed)
        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/todos/7", request.path)
        assertTrue(JSONObject(request.body.readUtf8()).getBoolean("completed"))
    }

    @Test
    fun `deleteTodo succeeds on 204`() = runTest {
        server.enqueue(MockResponse().setResponseCode(204))

        api.deleteTodo(3)

        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/api/todos/3", request.path)
    }

    @Test
    fun `deleteTodo throws on 404`() = runTest {
        server.enqueue(json("""{"error":"not found"}""", 404))

        try {
            api.deleteTodo(99)
            fail("expected TodoApiException")
        } catch (e: TodoApiException) {
            assertEquals(404, e.statusCode)
        }
    }
}
