import java.sql.*;
import javax.servlet.http.*;
import java.io.IOException;

public class SearchServletFormat extends HttpServlet {
    private Connection conn;

    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String term = req.getParameter("q");
        String sql = String.format("SELECT id FROM products WHERE title LIKE '%%%s%%'", term);
        try (Statement st = conn.createStatement()) {
            st.executeQuery(sql);
        } catch (SQLException e) {
            throw new IOException(e);
        }
    }
}
