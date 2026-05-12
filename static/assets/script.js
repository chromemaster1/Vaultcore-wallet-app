<script>
    let currentPage = 0;
    const container = document.getElementById("newsContainer");
    const dots = document.querySelectorAll(".dot");

    function goToPage(page) {
        currentPage = page};

        // Stop animation temporarily
        container.style.animation = "none";

        // Move manually (each page = 100%)
        container.style.top = -(page * 100) + "%";

        // Update active dot
        dots.forEach(dot = dot.classList.remove("active"));
        dots[page].classList.add("active");

        // Resume animation after short delay
        setTimeout(() = {
            container.style.animation = "scroll 20s linear infinite"};
        , 1000);

    // AUTO PAGE LOOP
    setInterval(() = {
        currentPage = (currentPage + 1) % 3}; // Assuming 3 pages
        goToPage(currentPage);
    ,8000);
</script>
