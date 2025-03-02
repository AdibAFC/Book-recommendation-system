import numpy as np
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pickle
import pandas as pd
from pymongo import MongoClient

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_generated_secret_key'  # Required for session handling

# Connect to MongoDB
MONGO_URI = "mongodb://127.0.0.1:27017/"
client = MongoClient(MONGO_URI)
db = client["book"]  # Database name
collection = db["user"]  # Collection name (like a table)

# Load CSV files
books_df = pd.read_csv('data/Books.csv')

# Load the saved models and data
with open('book_data.pkl', 'rb') as f:
    book_data = pickle.load(f)
with open('book_avg_rating.pkl', 'rb') as f:
    book_avg_rating = pickle.load(f)

pt_title = pickle.load(open('pt.pkl', 'rb'))
similarity_scores_title = pickle.load(open('similarity_scores.pkl', 'rb'))

pt_author = pickle.load(open('pt_author.pkl', 'rb'))
similarity_scores_author = pickle.load(open('similarity_scores_author.pkl', 'rb'))

pt_publisher = pickle.load(open('pt_publisher.pkl', 'rb'))
similarity_scores_publisher = pickle.load(open('similarity_scores_publisher.pkl', 'rb'))

all_book = pickle.load(open('all_book.pkl', 'rb'))

all_book_genre = pickle.load(open('all_book_genre.pkl', 'rb'))

# Home page
@app.route('/')
def index():
    merged_books = pd.merge(book_data, books_df[['ISBN', 'Image-URL-M']], left_on='isbn', right_on='ISBN', how='left')
    top_books = merged_books.head(20)
    return render_template('index.html', top_books=top_books, user=session.get('user_id'))


# User Registration
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        # Check if user already exists
        existing_user = collection.find_one({"email": email})
        if existing_user:
            flash("Email already registered. Please log in.", "warning")
            return redirect(url_for('login'))

        # Insert data into MongoDB
        collection.insert_one({"name": name, "email": email, "password": password})
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for('login'))

    return render_template("register.html")


# User Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        user = collection.find_one({"email": email})

        if user and user.get("password") == password:
            session['user_id'] = str(user['_id'])  # Store user session
            session['user_name'] = user['name']
            flash("Login successful!", "success")
            return redirect(url_for('recommend'))
        else:
            flash("Invalid email or password. Please try again.", "danger")

    return render_template('login.html')


# User Logout
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))


# Recommendation page (requires login)
# Connect to the user_history collection
history_collection = db["user_history"]  # Use the correct collection for user history

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    if 'user_id' not in session:
        flash('Please log in to access recommendations.', 'warning')
        return redirect(url_for('login'))

    user_input = request.form.get('search_query', '').strip().lower()
    search_type = request.form.get('search_type')

    # If the search query is empty, show the history from user_history collection
    if not user_input:
        user_history = history_collection.find_one({"user_id": session['user_id']})
        if user_history and "history" in user_history:
            recommended_books = user_history["history"]
        else:
            recommended_books = []
        return render_template('recommend.html', recommended_books=recommended_books)

    recommended_books = []

    if search_type == "genre":
        # Directly filter books based on genre matching
        matching_books = all_book_genre[
            all_book_genre['genre'].str.strip().str.lower().str.contains(user_input, na=False)]

        if matching_books.empty:
            flash("No books found for the specified genre.", "info")
            return render_template('recommend.html', recommended_books=[])

        # Extract relevant details
        recommended_books = matching_books[['book_title', 'book_author', 'image_url_m', 'publisher']].to_dict(
            orient='records')
    else:
        pt = None
        similarity_scores = None
        type_field = None

        if search_type == "title":
            pt = pt_title
            similarity_scores = similarity_scores_title
            type_field = 'book_title'
        elif search_type == "author":
            pt = pt_author
            similarity_scores = similarity_scores_author
            type_field = 'book_author'
        elif search_type == "publisher":
            pt = pt_publisher
            similarity_scores = similarity_scores_publisher
            type_field = 'publisher'
        else:
            flash("Invalid search type.", "danger")
            return render_template('recommend.html', recommended_books=[])

        # Normalize both sides for matching
        matching_items = [entry for entry in pt.index if user_input in entry.strip().lower()]

        if not matching_items:
            flash("No matches found. Please try again.", "info")
            return render_template('recommend.html', recommended_books=[])

        selected_item = matching_items[0]
        index = np.where(pt.index == selected_item)[0][0]
        similar_items = sorted(list(enumerate(similarity_scores[index])), key=lambda x: x[1], reverse=True)[1:5]

        # Collect the recommended books
        for i in similar_items:
            temp_df = all_book[all_book[type_field].str.strip().str.lower() == pt.index[i[0]].strip().lower()]
            if temp_df.empty:
                continue

            book = {
                'book_title': temp_df.drop_duplicates(type_field)['book_title'].values[0],
                'book_author': temp_df.drop_duplicates(type_field)['book_author'].values[0],
                'image_url_m': temp_df.drop_duplicates(type_field)['image_url_m'].values[0],
                'publisher': temp_df.drop_duplicates(type_field)['publisher'].values[0],
            }
            recommended_books.append(book)

    # Store the recommended books in user_history for future reference
    if 'user_id' in session:
        # Get the existing history
        user_history = history_collection.find_one({"user_id": session['user_id']})

        # If history exists, append the new recommendations, otherwise create a new history entry
        if user_history and "history" in user_history:
            # Get the current history
            existing_books = {book['book_title']: book for book in user_history["history"]}

            # Avoid adding duplicate books
            for book in recommended_books[:4]:  # Save only the top 4 books
                if book['book_title'] not in existing_books:
                    existing_books[book['book_title']] = book

            # Update the history list with the new, non-duplicate books
            user_history["history"] = list(existing_books.values())
        else:
            user_history = {"user_id": session['user_id'], "history": recommended_books[:4]}

        # Update the history in the database (or insert if not exists)
        history_collection.update_one(
            {"user_id": session['user_id']},
            {"$set": {"history": user_history["history"]}},
            upsert=True
        )

    return render_template('recommend.html', recommended_books=recommended_books)

if __name__ == '__main__':
    app.run(debug=True)
