import java.sql.*;

public class LoginDaoConcat {
    private final Connection conn;

    public LoginDaoConcat(Connection conn) {
        this.conn = conn;
    }

    public boolean exists(String username) throws SQLException {
        Statement st = conn.createStatement();
        ResultSet rs = st.executeQuery(
            "SELECT * FROM accounts WHERE username = '" + username + "'");
        return rs.next();
    }
}
