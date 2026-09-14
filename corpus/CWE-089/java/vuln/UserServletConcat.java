import java.io.*;
import java.sql.*;
import javax.servlet.http.*;

public class UserServletConcat extends HttpServlet {
    private Connection conn;

    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String id = req.getParameter("id");
        try {
            Statement st = conn.createStatement();
            ResultSet rs = st.executeQuery("SELECT name, email FROM users WHERE id = '" + id + "'");
            resp.getWriter().write(rs.toString());
        } catch (SQLException e) {
            throw new IOException(e);
        }
    }
}
