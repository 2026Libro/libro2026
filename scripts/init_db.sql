-- 개발용 DB와 전용 계정 생성 (root 계정으로 1회 실행)
-- 비밀번호는 원하는 값으로 바꾸고, .env의 DB_PASSWORD와 동일하게 맞춘다.

CREATE DATABASE IF NOT EXISTS library_robot
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'library_app'@'localhost' IDENTIFIED BY 'change-me';
-- 계정이 이미 있으면 위 줄은 비밀번호를 바꾸지 않으므로, 여기서 다시 맞춘다.
ALTER USER 'library_app'@'localhost' IDENTIFIED BY 'change-me';
GRANT ALL PRIVILEGES ON library_robot.* TO 'library_app'@'localhost';

-- 테스트 전용 DB (pytest가 사용. 개발 데이터와 섞이지 않도록 분리)
CREATE DATABASE IF NOT EXISTS library_robot_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON library_robot_test.* TO 'library_app'@'localhost';

FLUSH PRIVILEGES;
