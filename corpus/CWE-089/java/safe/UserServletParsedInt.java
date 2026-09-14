import java.io.*;
import java.sql.*;
import javax.servlet.http.*;

public class UserServletParsedInt extends HttpServlet {
    private Connection conn;

    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        int id = Integer.parseInt(req.getParameter("id"));
        try (Statement st = conn.createStatement()) {
            st.executeQuery("SELECT name FROM users WHERE id = " + id);
        } catch (SQLException e) {
            throw new IOException(e);
        }
    }
}
