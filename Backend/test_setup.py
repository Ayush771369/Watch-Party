"""
Simple test script to verify database setup and basic functionality.
"""
from app.database import Base, engine
from app.models import Video, Room, RoomState

def test_db_setup():
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Success: Database tables created")

    print("\nTables created:")
    for table_name in Base.metadata.tables.keys():
        print(f"  - {table_name}")

if __name__ == "__main__":
    test_db_setup()
