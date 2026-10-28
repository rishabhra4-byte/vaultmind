import { NavLink, Route, Routes } from "react-router-dom";
import { Empty, usePageTitle } from "../components/bits";
import Activity from "./admin/Activity";
import Glossary from "./admin/Glossary";
import Groups from "./admin/Groups";
import Overview from "./admin/Overview";
import People from "./admin/People";
import Sources from "./admin/Sources";
import Teams from "./admin/Teams";

const TABS: [string, string][] = [
  ["", "Overview"],
  ["people", "People"],
  ["teams", "Teams"],
  ["groups", "Groups"],
  ["sources", "Sources"],
  ["glossary", "Glossary"],
  ["activity", "Activity"],
];

export default function Admin() {
  usePageTitle("Admin");
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Admin</h1>
          <p className="muted">People, where documents come from, and what people ask.</p>
        </div>
      </header>
      <nav className="admin-tabs" aria-label="Admin sections">
        {TABS.map(([path, label]) => (
          <NavLink key={path} to={`/admin${path ? `/${path}` : ""}`} end={path === ""}>
            {label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Overview />} />
        <Route path="people" element={<People />} />
        <Route path="teams" element={<Teams />} />
        <Route path="groups" element={<Groups />} />
        <Route path="sources" element={<Sources />} />
        <Route path="glossary" element={<Glossary />} />
        <Route path="activity" element={<Activity />} />
        <Route path="*" element={<Empty title="There's no such admin section." />} />
      </Routes>
    </div>
  );
}
