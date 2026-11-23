import os

from sqlalchemy import select

from app.main import Organization, SessionLocal, User, UserRole, password_hash


def main() -> None:
    email = os.getenv("ECC_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ECC_ADMIN_PASSWORD", "")
    full_name = os.getenv("ECC_ADMIN_NAME", "ECC Administrator").strip()
    organization_name = os.getenv("ECC_ORGANIZATION_NAME", "Default Organization").strip()

    if not email or not password or not organization_name:
        raise SystemExit("Set ECC_ADMIN_EMAIL, ECC_ADMIN_PASSWORD and ECC_ORGANIZATION_NAME before running.")
    if len(password) < 12:
        raise SystemExit("ECC_ADMIN_PASSWORD must contain at least 12 characters.")

    with SessionLocal() as session:
        if session.scalar(select(User).where(User.email == email)):
            raise SystemExit("A user with this email already exists.")
        organization = session.scalar(select(Organization).where(Organization.name == organization_name))
        if organization is None:
            organization = Organization(name=organization_name)
            session.add(organization)
            session.flush()
        user = User(
            organization_id=organization.id,
            email=email,
            full_name=full_name,
            password_hash=password_hash.hash(password),
            role=UserRole.admin,
            is_active=True,
        )
        session.add(user)
        session.commit()
        print(f"Administrator created for organization: {organization.name}")


if __name__ == "__main__":
    main()
