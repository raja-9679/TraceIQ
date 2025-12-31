import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import UserProjectAccess, TeamProjectAccess, Role

async def fix_rbac_data():
    async with get_session_context() as session:
        # Fetch Roles
        roles_result = await session.exec(select(Role))
        roles = roles_result.all()
        role_map = {r.name.lower(): r.id for r in roles}
        
        print(f"Role Map: {role_map}")
        
        # Admin -> Admin
        # Editor -> Editor
        # Viewer -> Viewer
        # (Assuming role names are capitalized in DB)
        
        # Fix UserProjectAccess
        print("Fixing UserProjectAccess...")
        upas = await session.exec(select(UserProjectAccess).where(UserProjectAccess.role_id == None))
        for upa in upas.all():
            if upa.access_level:
                # Map 'admin' -> 'Admin', 'editor' -> 'Editor', etc.
                target_role_name = upa.access_level.capitalize()
                # Handle edge cases or specific mapping if needed
                if target_role_name == 'Owner': target_role_name = 'Admin' # Fallback if owner role missing? Or just map to Admin
                
                role_id = role_map.get(target_role_name.lower()) # check lowercase map
                
                # Try case insensitive match
                if not role_id:
                     for r_name, r_id in role_map.items():
                         if r_name.lower() == target_role_name.lower():
                             role_id = r_id
                             break
                
                if role_id:
                    print(f"Updating UPA User {upa.user_id} Project {upa.project_id}: {upa.access_level} -> Role ID {role_id}")
                    upa.role_id = role_id
                    session.add(upa)
                else:
                    print(f"Warning: No matching role found for access_level '{upa.access_level}'")
        
        # Fix TeamProjectAccess
        print("Fixing TeamProjectAccess...")
        tpas = await session.exec(select(TeamProjectAccess).where(TeamProjectAccess.role_id == None))
        for tpa in tpas.all():
            if tpa.access_level:
                target_role_name = tpa.access_level.capitalize()
                role_id = None
                for r_name, r_id in role_map.items():
                     if r_name.lower() == target_role_name.lower():
                         role_id = r_id
                         break
                
                if role_id:
                    print(f"Updating TPA Team {tpa.team_id} Project {tpa.project_id}: {tpa.access_level} -> Role ID {role_id}")
                    tpa.role_id = role_id
                    session.add(tpa)

        await session.commit()
        print("Done.")

if __name__ == "__main__":
    asyncio.run(fix_rbac_data())
