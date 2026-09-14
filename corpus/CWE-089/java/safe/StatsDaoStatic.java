import java.sql.*;

public class StatsDaoStatic {
    private final Connection conn;

    public StatsDaoStatic(Connection conn) {
        this.conn = conn;
    }

    public int userCount() throws SQLException {
        Statement st = conn.createStatement();
        ResultSet rs = st.executeQuery("SELECT count(*) FROM users");
        rs.next();
        return rs.getInt(1);
    }
}
