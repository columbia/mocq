import java.io.*;
import java.sql.*;
import javax.servlet.http.*;

public class UserServletPrepared extends HttpServlet {
    private Connection conn;

    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        String id = req.getParameter("id");
        try {
            PreparedStatement ps = conn.prepareStatement(
                "SELECT name, email FROM users WHERE id = ?");
            ps.setString(1, id);
            ResultSet rs = ps.executeQuery();
            resp.getWriter().write(rs.toString());
        } catch (SQLException e) {
            throw new IOException(e);
        }
    }
}
