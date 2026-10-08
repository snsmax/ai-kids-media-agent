// Only runs on a fresh MongoDB volume. Never embeds credentials in source.
const appDatabase = process.env.MONGO_DATABASE;
if (!appDatabase || !process.env.MONGO_APP_USERNAME || !process.env.MONGO_APP_PASSWORD) {
  throw new Error("MongoDB application account environment is required");
}
db.getSiblingDB(appDatabase).createUser({
  user: process.env.MONGO_APP_USERNAME,
  pwd: process.env.MONGO_APP_PASSWORD,
  roles: [{ role: "readWrite", db: appDatabase }],
});
