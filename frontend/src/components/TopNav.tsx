import { NavLink } from "react-router-dom";

export function TopNav() {
  return (
    <header className="topnav">
      <div className="brand">
        <div className="brand-mark">
          Net<span>Guard</span>
        </div>
        <div className="brand-sub">Fault mitigation agent</div>
      </div>
      <nav className="nav-links">
        <NavLink to="/" end>
          Console
        </NavLink>
        <NavLink to="/calls">Calls</NavLink>
        <NavLink to="/providers">Providers</NavLink>
        <NavLink to="/rag">RAG</NavLink>
      </nav>
    </header>
  );
}
