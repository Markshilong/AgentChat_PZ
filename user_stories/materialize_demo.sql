CREATE OR REPLACE TABLE paper_manifest AS
SELECT *
FROM (
    VALUES
        ('li2021', 2021, '/home/user/testdata/bdf-demo/li2021.pdf'),
        ('marques2021', 2021, '/home/user/testdata/bdf-demo/marques2021.pdf'),
        ('munkhbaatar2020', 2020, '/home/user/testdata/bdf-demo/munkhbaatar2020.pdf'),
        ('liu2024', 2024, '/home/user/testdata/bdf-demo/liu2024.pdf'),
        ('zethoven2022', 2022, '/home/user/testdata/bdf-demo/zethoven2022.pdf')
) AS manifest(paper_id, publication_year, pdf_path);

CREATE OR REPLACE VIEW paper_manifest_2021_onward AS
SELECT paper_id, publication_year, pdf_path
FROM paper_manifest
WHERE publication_year >= 2021
ORDER BY publication_year, paper_id;
